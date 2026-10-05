import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.usage import Usage

from knowledge_admin import documentation_updates as updates


def git(repository, *arguments):
    return subprocess.run(['git', '-C', str(repository), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    monkeypatch.delenv('AGENT_SHELL_URL', raising=False)
    output = tmp_path / 'documents-output'
    output.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(output))
    repository = tmp_path / 'code'
    repository.mkdir()
    git(repository, 'init', '-q')
    git(repository, 'config', 'user.email', 'fixture@example.test')
    git(repository, 'config', 'user.name', 'Fixture')
    (repository / 'api.py').write_text('def limit():\n    return 10\n')
    (repository / 'old name.txt').write_text('same content\n')
    (repository / 'removed.txt').write_text('obsolete\n')
    (repository / 'hidden.py').write_text('def important():\n    return "$Format:%H$"\n')
    (repository / '.gitattributes').write_text('hidden.py export-ignore export-subst\n')
    git(repository, 'add', '.')
    git(repository, 'commit', '-qm', 'Base service')
    base = git(repository, 'rev-parse', 'HEAD')
    (repository / 'api.py').write_text('def limit():\n    return 20\n')
    (repository / 'old name.txt').rename(repository / 'new\nname.txt')
    (repository / 'removed.txt').unlink()
    (repository / 'image.bin').write_bytes(b'\0binary image')
    outside = tmp_path / 'private.txt'
    outside.write_text('MUST_NOT_READ')
    (repository / 'external').symlink_to(outside)
    git(repository, 'add', '.')
    git(repository, 'update-index', '--add', '--cacheinfo', f'160000,{base},vendor')
    git(repository, 'commit', '-qm', 'Change limit and resources')
    target = git(repository, 'rev-parse', 'HEAD')
    (repository / 'api.py').write_text('uncommitted local work\n')
    (repository / 'untracked.txt').write_text('not in either snapshot\n')
    # External diff programs must not execute during a read-only comparison.
    git(repository, 'config', 'diff.external', f'touch {tmp_path / "executed"}')
    return repository, base, target, output


def call(comparison, **changes):
    repository, base, _, _ = comparison
    arguments = dict(service={'id': 'service-1', 'name': 'Example'}, repository_path=repository,
                     base_commit=base, documents={'overview': '# Service\n\nLimit is 10.\n'},
                     document_commits={'overview': base},
                     model={'enabled': True, 'api_key_ref': None}, credentials={})
    arguments.update(changes)
    return asyncio.run(updates.propose_service_documentation_update(**arguments))


def test_comparison_uses_exact_commits_and_preserves_checkout(comparison, monkeypatch):
    repository, base, target, output = comparison
    status = git(repository, 'status', '--porcelain')
    roots = []

    async def run(config, task, **kwargs):
        from knowledge_admin.agents import get_paths, get_tools
        root = Path(config['documents']['collections'][0]['paths'][0])
        roots.append(root)
        manifest = json.loads((root / 'comparison.json').read_text())
        assert manifest['base_commit'] == base and manifest['target_commit'] == target
        assert (root / 'base/api.py').read_text().endswith('return 10\n')
        assert (root / 'target/api.py').read_text().endswith('return 20\n')
        assert (root / 'base/removed.txt').exists()
        assert not (root / 'target/removed.txt').exists()
        assert (root / 'target/image.bin').read_bytes() == b'\0binary image'
        assert (root / 'target/hidden.py').read_text().endswith('return "$Format:%H$"\n')
        assert not (root / 'target/external').exists() and not (root / 'target/vendor').exists()
        assert not (root / 'target/untracked.txt').exists() and not (root / 'target/.git').exists()
        assert manifest['skipped_source_entries']['target'] == [
            {'path': 'external', 'reason': 'symlink'}, {'path': 'vendor', 'reason': 'submodule'}]
        assert {'status': 'R100', 'path': 'new\nname.txt', 'previous_path': 'old name.txt'} in manifest['changed_files']
        patch = (root / 'changes.patch').read_text()
        assert '-    return 10' in patch and '+    return 20' in patch and 'Binary files' in patch
        assert Path(manifest['documents']['overview']).read_text() == '# Service\n\nLimit is 10.\n'
        assert 'return 10' not in task and '# Service' not in task  # Evidence is read through tools.
        assert str(root / 'comparison.json') in task
        assert set(get_paths(config)) == {root, root / 'base', root / 'target'}
        assert 'shell_run' in {tool.name for tool in get_tools(config, [])}
        assert not config['ssh']['enabled'] and not config['mcp']['enabled']
        assert not config['code'].get('graphs')
        return SimpleNamespace(final_output=updates.DocumentationProposal(
            summary='The limit increased.', updates=[updates.DocumentUpdate(
                document_key='overview', edits=[{'before': 'Limit is 10.', 'after': 'Limit is 20.'}], reason='Limit changed from 10 to 20.',
                evidence=['base:api.py:2', 'target:api.py:2'])], unresolved_questions=[]),
            context_wrapper=SimpleNamespace(usage=Usage(requests=2, input_tokens=100, output_tokens=50, total_tokens=150)))

    monkeypatch.setattr(updates, 'agent', run)
    result = call(comparison)
    assert result['updates'][0]['document_key'] == 'overview'
    assert result['token_usage']['total_tokens'] == 150
    assert result['base_commit'] == base and result['target_commit'] == target
    assert result['document_commits'] == {'overview': target}
    assert base in result['change_report_markdown'] and target in result['change_report_markdown']
    assert 'Limit changed from 10 to 20.' in result['change_report_markdown']
    assert not any(root.exists() for root in roots) and not list(output.iterdir())
    assert git(repository, 'status', '--porcelain') == status
    assert git(repository, 'rev-parse', 'HEAD') == target
    assert (repository / 'api.py').read_text() == 'uncommitted local work\n'
    assert not (repository.parent / 'executed').exists()


def test_identical_trees_skip_model_even_for_different_commits(comparison, monkeypatch):
    repository, _, target, output = comparison
    git(repository, 'commit', '--allow-empty', '-qm', 'Metadata only')
    def unexpected(*args, **kwargs):
        pytest.fail('Identical trees must not invoke the model')
    monkeypatch.setattr(updates, 'agent', unexpected)
    result = call(comparison, base_commit=target, document_commits={'overview': target})
    assert result['base_commit'] != result['target_commit']
    assert result['changed_files'] == [] and result['updates'] == [] and result['token_usage'] is None
    assert not list(output.iterdir())


@pytest.mark.parametrize('revision', ['missing-revision', '--help', '', 'HEAD\0'])
def test_unavailable_or_invalid_revision_never_calls_agent(comparison, monkeypatch, revision):
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid comparisons must not invoke the model')
    monkeypatch.setattr(updates, 'agent', unexpected)
    with pytest.raises(ValueError):
        call(comparison, base_commit=revision)
    assert not list(comparison[3].iterdir())


def test_rejects_unrequested_updates_and_cleans_up(comparison, monkeypatch):
    async def run(*args, **kwargs):
        return SimpleNamespace(final_output=updates.DocumentationProposal(
            summary='Update', updates=[updates.DocumentUpdate(
                document_key=key, edits=[{'before': 'Limit is 10.', 'after': 'Limit is 20.'}], reason='Changed code', evidence=['target:api.py:2'])
                for key in ['unknown']], unresolved_questions=[]))
    monkeypatch.setattr(updates, 'agent', run)
    with pytest.raises(ValueError, match='unknown'):
        call(comparison)
    assert not list(comparison[3].iterdir())


def test_groups_multiple_updates_for_one_document_in_order():
    proposal = updates.DocumentationProposal(summary='Changed title and limit.', updates=[
        updates.DocumentUpdate(document_key='api', edits=[{'before': '# Service', 'after': '# API'}], reason='Rename.', evidence=['target:api.py:1']),
        updates.DocumentUpdate(document_key='api', edits=[{'before': 'Limit is 10.', 'after': 'Limit is 20.'}], reason='Increase limit.', evidence=['target:api.py:1', 'target:api.py:2']),
    ], unresolved_questions=[])
    result = updates._apply_updates({'api': '# Service\n\nLimit is 10.\n'}, proposal)
    assert len(result) == 1
    assert result[0]['markdown'] == '# API\n\nLimit is 20.\n'
    assert len(result[0]['edits']) == 2
    assert result[0]['evidence'] == ['target:api.py:1', 'target:api.py:2']
    assert result[0]['reason'] == 'Rename.\n\nIncrease limit.'
    proposal.updates.append(proposal.updates[0])
    with pytest.raises(updates.InvalidEdit):
        updates._apply_updates({'api': '# Service\n\nLimit is 10.\n'}, proposal)


@pytest.mark.parametrize('failure', [RuntimeError('provider failed'), asyncio.CancelledError()])
def test_failure_or_cancellation_removes_evidence(comparison, monkeypatch, failure):
    async def run(*args, **kwargs):
        raise failure
    monkeypatch.setattr(updates, 'agent', run)
    with pytest.raises(type(failure)):
        call(comparison)
    assert not list(comparison[3].iterdir())


def test_document_keys_are_not_treated_as_paths(comparison, monkeypatch):
    async def run(config, task, **kwargs):
        root = Path(config['documents']['collections'][0]['paths'][0])
        manifest = json.loads((root / 'comparison.json').read_text())
        path = Path(manifest['documents']['../../escape.md'])
        assert path == root / 'documents/1.md'
        return SimpleNamespace(final_output=updates.DocumentationProposal(
            summary='No documentation impact.', updates=[], unresolved_questions=['Binary content cannot be inspected.']),
            context_wrapper=SimpleNamespace(usage=Usage()))
    monkeypatch.setattr(updates, 'agent', run)
    result = call(comparison, documents={'../../escape.md': '# Current'}, document_commits={'../../escape.md': comparison[1]})
    assert result['updates'] == [] and result['unresolved_questions']
    assert not (comparison[3].parent / 'escape.md').exists()


def test_shell_requires_shared_evidence_folder(comparison, monkeypatch):
    monkeypatch.setenv('AGENT_SHELL_URL', 'http://shell-worker')
    monkeypatch.delenv('DOCUMENT_OUTPUT_ROOT')
    with pytest.raises(ValueError, match='shared mounted folder'):
        call(comparison)


def test_real_sdk_reads_both_snapshots_and_returns_structured_update(comparison, monkeypatch):
    import httpx
    from openai import AsyncOpenAI
    from knowledge_admin import model_adapter

    requests = []
    proposal = {'summary': 'The service now allows 20.', 'updates': [{
        'document_key': 'overview', 'edits': [{'before': 'Limit is 10.', 'after': 'Limit is 20.'}],
        'reason': 'The configured limit increased.', 'evidence': ['base:api.py:2', 'target:api.py:2']}],
        'unresolved_questions': []}

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers['Authorization'] == 'Bearer fixture-model-secret'
        assert 'fixture-model-secret' not in json.dumps(body)
        if len(requests) == 1:
            root = next(comparison[3].iterdir())
            output = [{'type': 'function_call', 'id': f'fc_{number}', 'call_id': f'call_{number}',
                       'name': 'read_file', 'arguments': json.dumps({'path': str(root / path)}), 'status': 'completed'}
                      for number, path in enumerate(('comparison.json', 'base/api.py', 'target/api.py', 'documents/1.md'))]
        else:
            results = [json.loads(item['output'])['content'] for item in body['input']
                       if item.get('type') == 'function_call_output']
            assert any('return 10' in text for text in results)
            assert any('return 20' in text for text in results)
            assert '# Service\n\nLimit is 10.\n' in results
            assert body['text']['format']['type'] == 'json_schema'
            output = [{'type': 'message', 'id': 'msg_result', 'role': 'assistant', 'status': 'completed',
                       'content': [{'type': 'output_text', 'text': json.dumps(proposal), 'annotations': []}]}]
        return httpx.Response(200, json={'id': f'resp_{len(requests)}', 'object': 'response', 'created_at': 0,
            'model': 'fixture', 'status': 'completed', 'output': output,
            'usage': {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150}})

    monkeypatch.setattr(model_adapter, 'AsyncOpenAI', lambda **kwargs: AsyncOpenAI(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))))
    model = {'provider': 'openai', 'api_format': 'responses', 'model': 'fixture',
             'base_url': 'http://model.test/v1', 'enabled': True, 'api_key_ref': {'id': 'model-key'}}
    result = call(comparison, model=model, credentials={'model-key': 'fixture-model-secret'})
    assert len(requests) == 2
    assert result['updates'][0]['markdown'] == '# Service\n\nLimit is 20.\n'
    assert result['updates'][0]['edits'] == proposal['updates'][0]['edits']
    assert result['token_usage']['total_tokens'] == 300
    assert not list(comparison[3].iterdir())


def test_wrong_document_baseline_rejected_before_model(comparison, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('A document from another revision must not reach the model')
    monkeypatch.setattr(updates, 'agent', unexpected)
    with pytest.raises(ValueError, match='exact base commit'):
        call(comparison, document_commits={'overview': comparison[2]})


@pytest.mark.parametrize('before', ['missing text', 'i'])
def test_nonunique_or_missing_edit_anchor_cannot_change_document(comparison, monkeypatch, before):
    calls = []
    async def run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(final_output=updates.DocumentationProposal(
            summary='Change', updates=[updates.DocumentUpdate(document_key='overview',
                edits=[{'before': before, 'after': 'new text'}], reason='Changed limit', evidence=['target:api.py:2'])],
            unresolved_questions=[]), context_wrapper=SimpleNamespace(usage=Usage()),
            to_input_list=lambda: [{'role': 'assistant', 'content': 'Completed analysis'}])
    monkeypatch.setattr(updates, 'agent', run)
    documents = {'overview': '# Service\n\nLimit is 10.\n'}
    with pytest.raises(ValueError, match='missing or ambiguous anchor'):
        call(comparison, documents=documents)
    assert documents == {'overview': '# Service\n\nLimit is 10.\n'}
    assert len(calls) == 2 and calls[1]['max_turns'] == 5
    assert not list(comparison[3].iterdir())


def test_corrects_edit_once_without_repeating_analysis_and_counts_both_runs(comparison, monkeypatch):
    calls = []
    history = [{'role': 'assistant', 'content': 'The source limit changed from 10 to 20.'}]
    async def run(config, task, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            assert kwargs['history'] == history
            assert 'edit 1' in task and '0 matches' in task
            assert 'Do not inspect source code again' in task
            assert Path(config['documents']['collections'][0]['paths'][0]).exists()
        return SimpleNamespace(final_output=updates.DocumentationProposal(
            summary='The limit changed.', updates=[updates.DocumentUpdate(document_key='overview',
                edits=[{'before': 'Limit is 10.' if len(calls) == 2 else 'Wrong anchor', 'after': 'Limit is 20.'}],
                reason='Changed limit', evidence=['base:api.py:2', 'target:api.py:2'])], unresolved_questions=[]),
            context_wrapper=SimpleNamespace(usage=Usage(requests=1, input_tokens=100, output_tokens=50, total_tokens=150)),
            to_input_list=lambda: history)
    monkeypatch.setattr(updates, 'agent', run)
    result = call(comparison)
    assert len(calls) == 2
    assert result['updates'][0]['markdown'] == '# Service\n\nLimit is 20.\n'
    assert result['token_usage']['requests'] == 2
    assert result['token_usage']['total_tokens'] == 300
    assert not list(comparison[3].iterdir())


def test_collect_commits_ignores_dirty_or_unversioned_source(comparison, tmp_path, monkeypatch):
    repo, _, target, _ = comparison
    monkeypatch.setenv('CODE_ROOT', str(repo.parent))
    project = {'id': 'project', 'folder_paths': [str(repo)]}
    assert updates.collect_source_commits([project]) == {}
    git(repo, 'restore', 'api.py')
    (repo / 'untracked.txt').unlink()
    (repo / 'vendor').mkdir()
    assert updates.collect_source_commits([project]) == {'project': target}
    folder = repo.parent / 'plain'
    folder.mkdir()
    assert updates.collect_source_commits([{'id': 'plain', 'folder_paths': [str(folder)]}]) == {}
