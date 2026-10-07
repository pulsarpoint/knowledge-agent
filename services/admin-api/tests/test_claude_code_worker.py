"""Claude Code worker building blocks: the fixed options, credentials only in the CLI environment, safe errors, run
events and the draft of a turn."""
from dataclasses import replace
from uuid import uuid4

import pytest
from knowledge_llm import RuntimeModel

from claude_code_fakes import (AssistantMessage, Recorder, ResultMessage, SystemMessage, TextBlock, ThinkingBlock,
                               ToolResultBlock, ToolUseBlock, UserMessage)
from knowledge_admin import claude_code_worker as worker
from knowledge_admin.claude_code import SYSTEM_PROMPT

BEDROCK = RuntimeModel(id=uuid4(), name='Claude', provider='bedrock', model='eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
                       api_format='native', base_url=None, aws_region='eu-central-1', api_key='bedrock-secret-token')
ANTHROPIC = RuntimeModel(id=uuid4(), name='Claude API', provider='anthropic', model='claude-sonnet-4-5', api_format='native',
                         base_url='https://api.anthropic.com/v1', aws_region=None, api_key='sk-ant-secret')
SESSION_ID = '0b5d2c3e-1f2a-4b5c-8d9e-0f1a2b3c4d5e'
SESSION = {'id': SESSION_ID, 'context_snapshot': {'folders': [
               {'path': '/work/data/code/app', 'kind': 'code_project', 'name': 'Payments API', 'code_project_id': 'p'},
               {'path': '/work/data/jira', 'kind': 'folder', 'name': 'jira'}]},
           'prompt_snapshot': {'system_prompt': SYSTEM_PROMPT}, 'claude_session_id': None}


def test_options_allow_only_read_tools_and_bash_in_the_chosen_folders(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_CLI', '/services/admin-api/.venv/bin/knowledge-claude-cli')
    options = worker.build_options(SESSION, BEDROCK)
    assert options['system_prompt'] == {'type': 'preset', 'preset': 'claude_code', 'append': SYSTEM_PROMPT}
    assert options['tools'] == options['allowed_tools'] == ['Read', 'Grep', 'Glob', 'Bash']
    assert {'Edit', 'Write', 'NotebookEdit', 'Agent', 'Task', 'Skill', 'WebFetch', 'WebSearch'} <= set(options['disallowed_tools'])
    assert (options['permission_mode'], options['setting_sources'], options['skills'], options['mcp_servers'],
            options['strict_mcp_config']) == ('dontAsk', [], [], {}, True)
    assert (options['cwd'], options['add_dirs']) == ('/work/data/code/app', ['/work/data/code/app', '/work/data/jira'])
    assert (options['model'], options['resume']) == (BEDROCK.model, None)
    assert options['cli_path'] == '/services/admin-api/.venv/bin/knowledge-claude-cli'
    assert not {'max_turns', 'max_budget_usd', 'agents', 'hooks', 'user'} & set(options)  # no limits, sub-agents or hooks
    assert worker.build_options({**SESSION, 'claude_session_id': 'cli-session'}, BEDROCK)['resume'] == 'cli-session'
    monkeypatch.delenv('CLAUDE_CODE_CLI')
    assert 'cli_path' not in worker.build_options(SESSION, BEDROCK)


def test_credentials_reach_only_the_cli_environment():
    assert worker.build_options(SESSION, BEDROCK)['env'] == {
        **worker.BASE_ENV, 'HOME': f'/home/claude/sessions/{SESSION_ID}', 'TMPDIR': f'/home/claude/sessions/{SESSION_ID}/tmp',
        'KNOWLEDGE_CLAUDE_SLOT': '1', 'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_REGION': 'eu-central-1',
        'AWS_BEARER_TOKEN_BEDROCK': 'bedrock-secret-token', 'ANTHROPIC_MODEL': BEDROCK.model,
        'ANTHROPIC_DEFAULT_HAIKU_MODEL': BEDROCK.model, 'ANTHROPIC_SMALL_FAST_MODEL': BEDROCK.model}
    env = worker.build_options(SESSION, ANTHROPIC)['env']
    assert (env['ANTHROPIC_API_KEY'], env['ANTHROPIC_BASE_URL'], env['ANTHROPIC_MODEL'], env['ANTHROPIC_DEFAULT_HAIKU_MODEL'],
            env['ANTHROPIC_SMALL_FAST_MODEL']) == (
        'sk-ant-secret', 'https://api.anthropic.com', 'claude-sonnet-4-5', 'claude-sonnet-4-5', 'claude-sonnet-4-5')
    assert 'CLAUDE_CODE_USE_BEDROCK' not in env and 'AWS_BEARER_TOKEN_BEDROCK' not in env
    assert worker.BASE_ENV == {'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
                               'DISABLE_AUTOUPDATER': '1', 'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1',
                               'CLAUDE_CODE_SUBPROCESS_ENV_SCRUB': '1', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1',
                               'SHELL': '/bin/bash'}
    slot = worker.build_options(SESSION, BEDROCK, 3)['env']
    assert (slot['KNOWLEDGE_CLAUDE_SLOT'], slot['HOME']) == ('3', f'/home/claude/sessions/{SESSION_ID}')  # HOME is the session's
    for bad in (0, 9):
        with pytest.raises(ValueError):
            worker.build_options(SESSION, BEDROCK, bad)
    options = worker.build_options(SESSION, BEDROCK)
    assert 'bedrock-secret-token' not in repr({key: value for key, value in options.items() if key != 'env'})
    for model in (replace(BEDROCK, model='amazon.nova-pro-v1:0'), replace(ANTHROPIC, api_key=None),
                  replace(BEDROCK, provider='openai', api_format='responses')):
        with pytest.raises(ValueError):
            worker.credential_env(model)
    with pytest.raises(ValueError, match='Set the AWS region of this connection in LLM Settings'):
        worker.credential_env(replace(BEDROCK, aws_region=None))


def test_provider_errors_are_made_safe_with_a_hint():
    message = worker.safe_error('ValidationException: Invocation of model ID anthropic.claude-sonnet-4-5 with on-demand '
                                'throughput isn’t supported. Retry with an inference profile. token=bedrock-secret-token',
                                ['bedrock-secret-token'])
    assert 'bedrock-secret-token' not in message and '[redacted]' in message
    assert message.endswith('Bedrock needs an inference profile ID for this model, such as '
                            'eu.anthropic.claude-sonnet-4-5-20250929-v1:0. Change the model ID in LLM Settings.')
    assert worker.safe_error('AccessDeniedException: no access', []).endswith(
        'Check the key and model access of this connection in LLM Settings.')
    assert worker.safe_error('x' * 5000, []) == 'x' * 500 + ' Check the model connection in LLM Settings, then send a message to retry.'
    assert worker.safe_error('', []).startswith('Claude Code stopped with an error. ')
    assert worker.safe_error('a\n\n   b', []).startswith('a b ')


def test_turn_messages_become_run_events_and_the_final_answer_is_the_draft():
    recorder = Recorder()
    turn = worker.Turn(recorder, secrets=[])
    bash_output = '\n'.join(f'line {n}' for n in range(30))
    for message in (
            SystemMessage('init', {'session_id': 'cli-session'}),
            AssistantMessage([ThinkingBlock('Look at the routes first.'),
                              ToolUseBlock('t1', 'Read', {'file_path': '/work/data/code/app/main.py'}),
                              ToolUseBlock('t2', 'Grep', {'pattern': 'retry', 'path': '/work/data/code/app'}),
                              ToolUseBlock('t3', 'Glob', {'pattern': '**/*.py'}),
                              ToolUseBlock('t4', 'Bash', {'command': 'ls -la /work/data/jira'})]),
            UserMessage([ToolResultBlock('t1', 'import app'),
                         ToolResultBlock('t4', [{'type': 'text', 'text': bash_output}], is_error=False)]),
            UserMessage('a plain user echo is ignored'),
            AssistantMessage([TextBlock('Draft below.'), TextBlock('   '), TextBlock('# Payments API')]),
            ResultMessage('success', False, 'cli-session', result='# Payments API\n\nRetries back off.',
                          usage={'input_tokens': 100, 'cache_read_input_tokens': 900, 'cache_creation_input_tokens': 50,
                                 'output_tokens': 40}, total_cost_usd=0.12)):
        turn.add(message)
    assert [kind for kind, _ in recorder.events] == ['reasoning', 'tool_call', 'tool_call', 'tool_call', 'tool_call',
                                                     'tool_result', 'tool_result', 'message', 'message', 'usage']
    calls = [payload for kind, payload in recorder.events if kind == 'tool_call']
    assert [(c['name'], c['call_id'], c['summary']) for c in calls] == [
        ('Read', 't1', '/work/data/code/app/main.py'), ('Grep', 't2', 'retry in /work/data/code/app'),
        ('Glob', 't3', '**/*.py'), ('Bash', 't4', 'ls -la /work/data/jira')]
    assert calls[0]['arguments'] == {'file_path': '/work/data/code/app/main.py'}
    read, bash = [payload for kind, payload in recorder.events if kind == 'tool_result']
    assert (read['name'], read['output'], read['is_error']) == ('Read', 'import app', False)
    assert bash['name'] == 'Bash' and bash['output'] == bash_output
    assert bash['preview'] == '\n'.join(f'line {n}' for n in range(20))
    assert recorder.events[0] == ('reasoning', {'text': 'Look at the routes first.'})
    assert recorder.events[-1] == ('usage', {'input_tokens': 1050, 'output_tokens': 40, 'cached_tokens': 900,
                                             'total_tokens': 1090, 'cost_usd': 0.12})
    assert (turn.draft, turn.session_id, turn.usage['output_tokens']) == ('# Payments API\n\nRetries back off.', 'cli-session', 40)
    fallback = worker.Turn(Recorder(), secrets=[])
    fallback.add(AssistantMessage([TextBlock('Only text')]))
    fallback.add(ResultMessage('success', False, 's', result=''))
    assert fallback.draft == 'Only text'
    failed = Recorder()
    worker.Turn(failed, secrets=[]).add(AssistantMessage([], error='rate_limit'))
    assert failed.events == [('error', {'message': 'The model reported an error: rate_limit'})]
    assert worker.result_error(None) == 'Claude Code ended the turn without a result.'
    assert worker.result_error(ResultMessage('error_during_execution', True, 's', result='Overloaded', errors=['retry later'])) == 'Overloaded retry later'


def test_no_credential_reaches_events_logs_or_errors(caplog):
    caplog.set_level('DEBUG')
    secrets = ['bedrock-secret-token', 'sk-ant-secret', 'encryption-key-material']
    recorder = Recorder()
    turn = worker.Turn(recorder, secrets=secrets)
    for message in (
            SystemMessage('init', {'session_id': 'cli-session'}),
            AssistantMessage([ToolUseBlock('t1', 'Bash', {'command': 'echo sk-ant-secret; env | grep AWS'})]),
            UserMessage([ToolResultBlock('t1', [{'type': 'text', 'text': 'AWS_BEARER_TOKEN_BEDROCK=bedrock-secret-token'}],
                                         is_error=False)]),
            AssistantMessage([TextBlock('Done.')], error='authentication_failed'),
            ResultMessage('error_during_execution', True, 'cli-session', result='',
                          errors=['401 Unauthorized'])):
        turn.add(message)
    worker.build_options(SESSION, BEDROCK)
    worker.build_options(SESSION, ANTHROPIC)
    errors = [worker.safe_error(f'{type(exc).__name__}: {exc}', secrets) for exc in (
        RuntimeError('Bearer bedrock-secret-token rejected'),
        PermissionError('x-api-key: "sk-ant-secret" invalid; key=encryption-key-material'))]
    errors.append(worker.safe_error(worker.result_error(turn.result), secrets))
    produced = repr(recorder.events) + repr(errors) + caplog.text
    assert not [secret for secret in secrets if secret in produced]
    result = next(payload for kind, payload in recorder.events if kind == 'tool_result')
    assert result['output'] == result['preview'] == 'AWS_BEARER_TOKEN_BEDROCK=[redacted]'
    assert turn.tools == {'t1': 'Bash'}
    assert errors[0].count('[redacted]') == 1 and errors[1].count('[redacted]') == 2


def test_tool_output_and_preview_are_capped():
    recorder = Recorder()
    turn = worker.Turn(recorder, secrets=[])
    turn.add(AssistantMessage([ToolUseBlock('t1', 'Bash', {'command': 'cat big.log'})]))
    turn.add(UserMessage([ToolResultBlock('t1', 'x' * 100_000)]))
    [(_, result)] = [event for event in recorder.events if event[0] == 'tool_result']
    assert result['output'] == 'x' * worker.MAX_TOOL_OUTPUT + worker.TOOL_TRUNCATED
    assert result['preview'] == 'x' * worker.MAX_PREVIEW_CHARS + worker.TOOL_TRUNCATED
    assert (worker.MAX_TOOL_OUTPUT, worker.MAX_PREVIEW_CHARS) == (64 * 1024, 2000)
    secret = 'bedrock-secret-token'
    split = Recorder()
    worker.Turn(split, secrets=[secret]).add(UserMessage([ToolResultBlock('t2', 'y' * (worker.MAX_TOOL_OUTPUT - 8) + secret)]))
    assert 'bedrock' not in repr(split.events)  # redacted before the cap, so no partial key survives the cut
    with pytest.raises(TypeError):
        worker.Turn(recorder)  # secrets are required: a caller cannot forget the key


def test_fakes_match_the_sdk_message_types():
    import dataclasses
    sdk = pytest.importorskip('claude_agent_sdk')
    import claude_code_fakes as fakes
    for name in ('TextBlock', 'ThinkingBlock', 'ToolUseBlock', 'ToolResultBlock', 'AssistantMessage', 'UserMessage',
                 'SystemMessage', 'ResultMessage'):
        real = {field.name for field in dataclasses.fields(getattr(sdk, name))}
        fake = {field.name for field in dataclasses.fields(getattr(fakes, name))}
        assert fake <= real, (name, fake - real)
    options = {field.name for field in dataclasses.fields(sdk.ClaudeAgentOptions)}
    assert set(worker.build_options(SESSION, BEDROCK)) | {'cli_path', 'stderr'} <= options


def test_null_characters_never_reach_events_or_the_draft():
    recorder = Recorder()
    turn = worker.Turn(recorder, secrets=['sk-ant-secret'])
    turn.add(AssistantMessage([ToolUseBlock('t1', 'Bash', {'command': 'cat a.bin\x00', 'k\x00': 1})]))
    turn.add(UserMessage([ToolResultBlock('t1', 'ELF\x00\x00 sk-ant\x00-secret')]))
    turn.add(AssistantMessage([TextBlock('# Doc\x00')]))
    turn.add(ResultMessage('success', False, 's', result='# Doc\x00 sk-ant-sec\x00ret'))
    assert '\\u0000' not in repr(recorder.events) and '\x00' not in repr(recorder.events)
    result = next(payload for kind, payload in recorder.events if kind == 'tool_result')
    assert result['output'] == 'ELF [redacted]'
    assert worker.clean_text(turn.draft, ['sk-ant-secret']) == '# Doc [redacted]'
    assert '\x00' not in worker.safe_error('boom\x00', [])


def test_session_home_is_handed_to_the_slot_user_without_following_links(tmp_path):
    """own_tree needs only CAP_CHOWN: each directory is root's before it is listed, then the slot user's after."""
    from knowledge_admin import claude_code_cli as cli
    home = tmp_path / 'home'
    (home / '.claude/projects/app').mkdir(parents=True)
    (home / '.claude/projects/app/session.jsonl').write_text('{}')
    (home / 'link').symlink_to('/etc')
    (tmp_path / 'outside').write_text('x')
    (home / 'hard').hardlink_to(tmp_path / 'outside')
    calls = []

    class Os:
        lstat, listdir, mkdir = staticmethod(__import__('os').lstat), staticmethod(__import__('os').listdir), None

        @staticmethod
        def lchown(path, uid, gid):
            calls.append(('chown', path.removeprefix(str(home)) or '/', uid))

        @staticmethod
        def chmod(path, mode):
            calls.append(('chmod', path.removeprefix(str(home)) or '/', mode))

    cli.own_tree(str(home), 1003, 1001, Os)
    owned = {path for kind, path, uid in calls if kind == 'chown' and uid == 1003}
    assert owned == {'/', '/.claude', '/.claude/projects', '/.claude/projects/app', '/.claude/projects/app/session.jsonl', '/link'}
    assert '/hard' not in {path for _, path, _ in calls}  # another hard link: never handed over
    for folder in ('/', '/.claude', '/.claude/projects', '/.claude/projects/app'):
        root, mode, given = calls.index(('chown', folder, 0)), calls.index(('chmod', folder, 0o700)), calls.index(('chown', folder, 1003))
        assert root < mode < given
        children = [i for i, (kind, path, uid) in enumerate(calls) if path.startswith(folder.rstrip('/') + '/') and path != folder]
        assert all(mode < i < given for i in children), folder
    assert ('chmod', '/link', 0o700) not in calls


def test_home_root_belongs_to_root_and_hides_what_an_earlier_image_left(tmp_path, monkeypatch):
    import os
    root = tmp_path / 'claude'
    (root / '.claude').mkdir(parents=True)
    owners = []
    monkeypatch.setattr(os, 'lchown', lambda path, uid, gid: owners.append((path.removeprefix(str(root)), uid, gid)))
    worker.prepare_home_root(str(root))
    assert (root / 'sessions').is_dir()
    assert ('', 0, 0) in owners and ('/sessions', 0, 0) in owners and ('/.claude', 0, 0) in owners
    assert oct((root / 'sessions').stat().st_mode & 0o777) == oct(0o711) and oct(root.stat().st_mode & 0o777) == oct(0o711)
    assert oct((root / '.claude').stat().st_mode & 0o777) == oct(0o700)
    assert oct((root / 'tmp').stat().st_mode & 0o777) == oct(0o700)  # the worker's TMPDIR
