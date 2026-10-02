"""Run scoped source-only RLM steps, then synthesize their evidenced findings."""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import dspy

from api_source_rlm import prepare_source_task
from rlm_evidence import SourceEvidence, check_answers, sql_inventory
from rlm_handler import public_calls, saved_connection
from rlm_service import validate_context
from rlm_staged import run_staged


def scoped_selections(selection: dict, scopes: list[dict]) -> list[dict]:
    """Assign each focused endpoint question to exactly one scope; synthesis owns the overview."""
    questions = {q['id']: q for q in [*selection['handler_questions'], *selection['synthesis_questions']]}
    assigned = [qid for scope in scopes for qid in scope['question_ids']]
    if len(set(assigned)) != len(assigned) or set(assigned) != set(questions) - {'api.flow'}:
        raise ValueError('Scopes must partition the focused questions exactly once')
    ids = [scope['id'] for scope in scopes]
    if len(set(ids)) != len(ids) or any(not name.replace('_', '').isalnum() for name in ids):
        raise ValueError('Scope IDs must be unique simple directory names')
    return [{**selection, 'scope': scope['scope'],
             'handler_questions': [*[questions[qid] for qid in scope['question_ids']],
                                   {'id': f"scope.{scope['id']}", 'question': scope['findings'],
                                    'allowed_values': ['described']}],
             'synthesis_questions': []} for scope in scopes]


def synthesis_evidence(files: dict[str, str], selection: dict, steps: list[dict]) -> tuple[SourceEvidence, dict]:
    """Merge cited source and complete enclosing Go functions without consulting a graph."""
    evidence = SourceEvidence({'files': files, 'graphs': {}}, sql_inventory(files))
    selected = {}

    def include(path: str, start: int, end: int) -> None:
        source = evidence.get_source_range(path, start, end)
        selected.setdefault(path, {}).update(enumerate(source['code'].splitlines(), start))

    for step in steps:
        checks = check_answers(step['answers'], step['questions'], files)
        if checks['errors']:
            raise ValueError('Cannot synthesize invalid step citations')
        for answer in step['answers']:
            for citation in answer['evidence']:
                include(citation['path'], citation['start_line'], citation['end_line'])
    prefix = selection['project_id'] + '/'
    handler_path = prefix + selection['handler']['path']
    definitions = [m for m in evidence.scan_go(handler_path) if m['ruleId'] == 'definition'
                   and m['metaVariables']['single']['NAME']['text'] == selection['handler']['name']]
    if len(definitions) != 1:
        raise ValueError('Handler definition is missing or ambiguous in source')
    handler = definitions[0]
    include(handler_path, handler['range']['start']['line'] + 1, handler['range']['end']['line'] + 1)
    for route in selection['route_ranges']:
        include(prefix + route['path'], route['start_line'], route['end_line'])
    for path, lines in list(selected.items()):
        cited = set(lines)
        if path.endswith('.go'):
            for match in evidence.scan_go(path):
                start, end = match['range']['start']['line'] + 1, match['range']['end']['line'] + 1
                if match['ruleId'] == 'import' or (match['ruleId'] == 'definition' and any(start <= n <= end for n in cited)):
                    include(path, start, end)
        if path.endswith('.sql'):
            for statement in evidence.inventory['statements'].values():
                if statement['path'] == path and any(statement['start_line'] <= n <= statement['end_line'] for n in cited):
                    include(path, statement['start_line'], statement['end_line'])
    file_ids = {path: f'S{i}' for i, path in enumerate(sorted(selected), 1)}
    fragments = [{'id': file_ids[path], 'path': path, 'start_line': min(lines), 'end_line': max(lines),
                  'lines': [{'line': n, 'text': lines[n]} for n in sorted(lines)]}
                 for path, lines in sorted(selected.items())]
    observations = {step['id']: {'scope': step['scope'], 'answers': [
        {**{k: v for k, v in answer.items() if k != 'evidence'},
         'evidence': [{'fragment_id': file_ids[c['path']], 'start_line': c['start_line'], 'end_line': c['end_line']}
                      for c in answer['evidence']]} for answer in step['answers']]} for step in steps}
    return evidence, {
        'task': {'id': 'synthesis', 'endpoint': selection['endpoint'], 'mode': 'synthesis', 'resources': [],
                 'questions': [*selection['handler_questions'], *selection['synthesis_questions']]},
        'fragments': fragments, 'observations': observations,
        'gaps': ['Step findings are interpretations; check them against source.',
                 'Source ranges can contain gaps; cite only contiguous supplied lines.',
                 'No graph-derived targets or previous benchmark findings were supplied.'],
    }


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--scopes', type=Path, required=True)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name: str, value: object) -> None:
        (args.output / name).write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')

    context = json.loads(args.context.read_text(encoding='utf-8'))
    validate_context(context)
    selection = json.loads(args.selection.read_text(encoding='utf-8'))
    inputs = prepare_source_task(context, selection)
    del context
    scopes = json.loads(args.scopes.read_text(encoding='utf-8'))
    selections = scoped_selections(selection, scopes)
    connection = saved_connection(repo, args.model_id)
    model, key = connection['model'], connection.pop('key')
    if model['provider'] != 'openai' or model['api_format'] != 'responses':
        raise ValueError('This experiment requires the saved OpenAI-compatible Responses model')
    save('manifest.json', {'model': model, 'credential_source': 'LLM Settings',
        'context_sha256': hashlib.sha256(args.context.read_bytes()).hexdigest(),
        'source_hash': selection['snapshot_source_hash'], 'scopes': scopes,
        'graphs_exposed': False, 'expected_answers_visible_to_model': False, 'reasoning_effort': 'max'})
    (args.output / 'runner.py').write_text(Path(__file__).read_text(encoding='utf-8'), encoding='utf-8')
    synthesis_prompt = (repo / 'docs/prompts/api-node-staged.md').read_text(encoding='utf-8')
    (args.output / 'synthesis-instructions.md').write_text(synthesis_prompt, encoding='utf-8')
    save('status.json', {'status': 'running', 'phase': 'handler'})
    usages, steps = [], []
    started = time.monotonic()
    try:
        for scope, scoped in zip(scopes, selections, strict=True):
            step = scope['id']
            selection_path = args.output / f'{step}-selection.json'
            selection_path.write_text(json.dumps(scoped, indent=2), encoding='utf-8')
            save('status.json', {'status': 'running', 'phase': step})
            print(f'Bounded step: {step}', flush=True)
            run = subprocess.run([sys.executable, str(repo / 'scripts/benchmarks/api_source_rlm.py'),
                '--context', str(args.context.resolve()), '--selection', str(selection_path.resolve()),
                '--expected', str(args.expected.resolve()), '--model-id', args.model_id,
                '--prompt', str(repo / 'docs/prompts/api-bounded-source-rlm.md'),
                '--output', str((args.output / step).resolve())], cwd=repo, check=False)
            directory = args.output / step
            if (directory / 'usage.json').exists():
                usages.append({'step': step, **json.loads((directory / 'usage.json').read_text(encoding='utf-8'))})
                save('usage.json', usages)
            if run.returncode != 0:
                raise RuntimeError(f'Bounded step failed: {step}')
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
            if manifest['model'] != model:
                raise ValueError('LLM Settings changed during the experiment')
            result = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
            if result['status'] != 'completed' or result['forced_extraction']:
                raise ValueError('Bounded step did not submit a valid answer')
            steps.append({'id': step, 'scope': scope['scope'], 'questions': scoped['handler_questions'],
                          'answers': result['answers']})
        evidence, bundle = synthesis_evidence(inputs['sources'], selection, steps)
        directory = args.output / 'synthesis'
        directory.mkdir()
        save('status.json', {'status': 'running', 'phase': 'synthesis'})
        dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
        child = dspy.LM(model=f"openai/{model['model']}", model_type='responses',
            api_base=model['base_url'], api_key=key, reasoning={'effort': 'max'},
            response_format={'type': 'json_object'}, store=False, cache=False, num_retries=0, timeout=600)
        try:
            result = run_staged(evidence, bundle['task'], child, synthesis_prompt, directory,
                                prepared_evidence=bundle, allow_evidence_gaps=True)
            save('synthesis/result.json', result)
        finally:
            calls = public_calls(child, 'child')
            save('synthesis/calls.json', calls)
            usage = {'step': 'synthesis', 'calls': len(calls),
                'input_tokens': sum(c['usage'].get('input_tokens', 0) for c in calls),
                'cached_input_tokens': sum((c['usage'].get('input_tokens_details') or {}).get('cached_tokens', 0) or 0 for c in calls),
                'output_tokens': sum(c['usage'].get('output_tokens', 0) for c in calls),
                'reasoning_tokens': sum((c['usage'].get('output_tokens_details') or {}).get('reasoning_tokens', 0) or 0 for c in calls)}
            usage['total_tokens'] = usage['input_tokens'] + usage['output_tokens']
            usages.append(usage)
            save('usage.json', usages)
        # Expected facts are evaluated after all generation; never supplied to synthesis.
        expected = json.loads(args.expected.read_text(encoding='utf-8'))
        if expected['snapshot_source_hash'] != selection['snapshot_source_hash']:
            raise ValueError('Expected answers belong to another snapshot')
        answers = {a['id']: a for a in result['answers']}
        checks = [{'id': qid, 'expected': value, 'actual': answers.get(qid, {}).get('value'),
                   'correct': answers.get(qid, {}).get('value') == value}
                  for qid, value in expected['answers'].items()]
        save('evaluation.json', {'checks': checks, 'correct': sum(c['correct'] for c in checks),
                                'total': len(checks), 'note': 'Targeted values; narrative still requires source review.'})
        (args.output / 'overview.md').write_text(answers['api.flow']['explanation'] + '\n', encoding='utf-8')
        save('status.json', {'status': 'completed', 'seconds': time.monotonic() - started})
    except Exception as exc:
        save('status.json', {'status': 'failed', 'error_type': type(exc).__name__, 'seconds': time.monotonic() - started})
        raise
    finally:
        totals = {field: sum(u.get(field, 0) for u in usages) for field in
                  ('calls', 'input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_tokens', 'total_tokens')}
        totals['uncached_input_tokens'] = totals['input_tokens'] - totals['cached_input_tokens']
        save('total-usage.json', totals)
        print(json.dumps(totals), flush=True)


if __name__ == '__main__':
    main()
