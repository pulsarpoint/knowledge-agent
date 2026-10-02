"""Benchmark individual functions with extracted source and mechanical citations."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import dspy
from litellm import model_cost

from bedrock_analysis import BedrockAnalysis
from function_evidence import collect_function_evidence, requested_resources
from rlm_evidence import SourceEvidence
from rlm_handler import public_calls, saved_connection
from rlm_service import validate_context
from rlm_staged import run_staged


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--model-id')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-rounds', type=int, default=3,
                        help='One initial call plus targeted dependency requests; unresolved work stays incomplete')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.max_rounds < 1 or (not args.prepare_only and args.model_id is None):
        parser.error('Use a positive --max-rounds and a saved --model-id for model execution')
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name: str, value: object) -> None:
        (args.output / name).write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')

    context = json.loads(args.context.read_text(encoding='utf-8'))
    validate_context(context)
    selection = json.loads(args.selection.read_text(encoding='utf-8'))
    project = next(p for p in context['projects'] if p['id'] == selection['project_id'])
    if project['source_hash'] != selection['snapshot_source_hash']:
        raise ValueError('Selection belongs to a different source snapshot')
    files = {path: text for path, text in context['files'].items()
             if path.startswith(project['id'] + '/')}
    evidence = SourceEvidence({'files': files, 'graphs': {}}, {})
    del context
    prompt = (repo / 'docs/prompts/api-function-unit.md').read_text(encoding='utf-8')
    (args.output / 'instructions.md').write_text(prompt, encoding='utf-8')
    for name in ('api_function_steps.py', 'function_evidence.py', 'rlm_staged.py', 'rlm_evidence.py',
                 'bedrock_analysis.py', 'rlm_handler.py'):
        (args.output / name).write_text((Path(__file__).parent / name).read_text(encoding='utf-8'), encoding='utf-8')
    save('selection.json', selection)
    save('manifest.json', {'source_hash': project['source_hash'],
        'context_sha256': hashlib.sha256(args.context.read_bytes()).hexdigest(),
        'graphs_exposed': False, 'expected_answers_visible_to_model': False,
        'credential_source': 'LLM Settings', 'max_rounds': args.max_rounds})
    tasks = []
    for unit in selection['units']:
        if not unit['id'].replace('_', '').isalnum():
            raise ValueError('Use simple unit IDs')
        resources = [{**r, 'path': project['id'] + '/' + r['path']} for r in unit['resources']]
        task = {'id': unit['id'], 'mode': 'function', 'scope': unit['scope'],
                'questions': unit['questions'], 'resources': resources}
        bundle = collect_function_evidence(evidence, task, resources)
        save(f"{unit['id']}-prepared.json", bundle)
        tasks.append((task, bundle))
    if args.prepare_only:
        save('status.json', {'status': 'prepared', 'units': len(tasks)})
        return

    connection = saved_connection(repo, args.model_id)
    model, key = connection['model'], connection.pop('key')
    save('model.json', model)
    dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
    if model['provider'] == 'openai' and model['api_format'] == 'responses':
        child = dspy.LM(model=f"openai/{model['model']}", model_type='responses', api_base=model['base_url'],
            api_key=key, reasoning={'effort': 'max'}, response_format={'type': 'json_object'},
            store=False, cache=False, num_retries=0, timeout=600)
    elif model['provider'] == 'bedrock' and model['api_format'] == 'native':
        capabilities = model_cost.get(model['model'], {})
        if not capabilities.get('supports_xhigh_reasoning_effort') or not capabilities.get('max_output_tokens'):
            raise ValueError('Select a Claude Bedrock model with known xhigh support and output allowance')
        child = BedrockAnalysis(model, key, effort='xhigh', max_tokens=capabilities['max_output_tokens'])
        save('transport.json', {'api': 'Bedrock InvokeModel', 'thinking': 'adaptive', 'effort': child.effort,
            'max_tokens': child.max_tokens, 'allowance_source': 'installed LiteLLM model capabilities',
            'json_mode': 'prompt only', 'cache_checkpoints': ['system', 'last message'],
            'reasoning_tokens': 'unavailable separately; included in output', 'retries': 0})
    else:
        raise ValueError('Select a saved Responses or native Claude Bedrock connection')
    results, usage = [], []
    started = time.monotonic()
    try:
        for task, bundle in tasks:
            resources = list(task['resources'])
            for round_number in range(1, args.max_rounds + 1):
                name = f"{task['id']}/round-{round_number}"
                directory = args.output / name
                directory.mkdir(parents=True)
                save('status.json', {'status': 'running', 'unit': task['id'], 'round': round_number})
                before = len(child.history)
                try:
                    result = run_staged(evidence, task, child, prompt, directory,
                                        prepared_evidence=bundle, allow_evidence_gaps=True)
                    save(f'{name}/result.json', result)
                finally:
                    calls = public_calls(child, 'analysis')[before:]
                    save(f'{name}/calls.json', calls)
                    record = {'unit': task['id'], 'round': round_number, 'calls': len(calls),
                        'input_tokens': sum(c['usage'].get('input_tokens', 0) for c in calls),
                        'cached_input_tokens': sum((c['usage'].get('input_tokens_details') or {}).get('cached_tokens', 0) or 0 for c in calls),
                        'cache_creation_input_tokens': sum(c['usage'].get('cache_creation_input_tokens', 0) for c in calls),
                        'output_tokens': sum(c['usage'].get('output_tokens', 0) for c in calls),
                        'reasoning_tokens': None}
                    reasoning = [(c['usage'].get('output_tokens_details') or {}).get('reasoning_tokens') for c in calls]
                    if all(value is not None for value in reasoning):
                        record['reasoning_tokens'] = sum(reasoning)
                    record['total_tokens'] = record['input_tokens'] + record['output_tokens']
                    usage.append(record)
                    save('usage.json', usage)
                    print(json.dumps(record), flush=True)
                requests = result['needs_evidence']
                if not requests:
                    break
                additions, gaps = requested_resources(bundle, requests, resources)
                save(f'{name}/dependencies.json', {'requested': requests, 'added': additions, 'unavailable': gaps})
                if not additions:
                    break  # No evidence gained; preserve unknowns instead of another identical call.
                resources.extend(additions)
                bundle = collect_function_evidence(evidence, task, resources)
                bundle['gaps'].extend(gaps)
            result['status'] = 'needs_evidence' if result['needs_evidence'] else 'completed'
            results.append(result)
            save(f"{task['id']}/result.json", result)
            (args.output / task['id'] / 'findings.md').write_text('\n\n'.join(
                f"### {a['id']} — {a['value']}\n\n{a['explanation']}" for a in result['answers']) + '\n', encoding='utf-8')
        # The answer key is never read until all model calls have finished.
        expected = json.loads(args.expected.read_text(encoding='utf-8'))
        if expected['snapshot_source_hash'] != selection['snapshot_source_hash']:
            raise ValueError('Expected values belong to another snapshot')
        answers = {a['id']: a for result in results for a in result['answers']}
        checks = [{'id': qid, 'expected': value, 'actual': answers.get(qid, {}).get('value'),
                   'correct': answers.get(qid, {}).get('value') == value} for qid, value in expected['answers'].items()]
        save('evaluation.json', {'checks': checks, 'correct': sum(c['correct'] for c in checks), 'total': len(checks),
                                'note': 'Values only; explanation accuracy requires separate source review.'})
        save('status.json', {'status': 'completed' if all(r['status'] == 'completed' for r in results) else 'needs_evidence',
                             'seconds': time.monotonic() - started})
        if any(r['status'] != 'completed' for r in results):
            raise SystemExit(2)
    except Exception as exc:
        save('status.json', {'status': 'failed', 'error_type': type(exc).__name__, 'seconds': time.monotonic() - started})
        raise
    finally:
        totals = {field: sum(u[field] for u in usage) if all(u[field] is not None for u in usage) else None
                  for field in ('calls', 'input_tokens', 'cached_input_tokens', 'cache_creation_input_tokens',
                                'output_tokens', 'reasoning_tokens', 'total_tokens')}
        totals['uncached_input_tokens'] = totals['input_tokens'] - totals['cached_input_tokens']
        save('total-usage.json', totals)


if __name__ == '__main__':
    main()
