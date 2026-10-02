"""Run focused state/worker RLM questions over a deterministic frozen inventory."""

import argparse
import hashlib
import importlib.metadata
import json
import re
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import dspy
from openai import APIStatusError, OpenAI

from rlm_evidence import SourceEvidence, check_answers, sql_inventory
from rlm_handler import public_calls, saved_connection
from rlm_native import NativePythonStep
from rlm_protocol import ProtocolLog, protocol_calls
from rlm_service import ExecutionLog, validate_context
from rlm_staged import run_staged


class StateQuestions(dspy.Signature):
    service_context: dict[str, Any] = dspy.InputField(desc='Frozen whole-service source/graphs in REPL variables. Use the tested tools for this task.')
    catalogue: list[dict[str, Any]] = dspy.InputField(desc='Deterministic declared-table candidates. Schema-unknown and qualified declarations remain distinct.')
    task: dict[str, Any] = dspy.InputField(desc='One focused resource group, specific questions and allowed answer values. No expected answers.')
    answers: list[dict[str, Any]] = dspy.OutputField(desc='Exactly one {id,value,explanation,evidence:[{path,start_line,end_line,quote}]} per question.')


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--model-id')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--questions', type=Path, default=repo / 'scripts/benchmarks/fixtures/rlm_state_questions.json')
    parser.add_argument('--expected', type=Path, default=repo / 'scripts/benchmarks/fixtures/rlm_state_expected.json')
    parser.add_argument('--prompt', type=Path)
    parser.add_argument('--workflow', choices=('rlm', 'staged'), default='rlm',
                        help='Use autonomous RLM exploration or fixed evidence → child analysis → validation → submission')
    parser.add_argument('--case', action='append', help='Run only these case IDs; repeat to select several')
    parser.add_argument('--max-iters', type=int, default=8)
    parser.add_argument('--max-subcalls', type=int, default=4)
    parser.add_argument('--reasoning-effort', default='max')
    parser.add_argument('--adapter', choices=('chat', 'json'), default='chat')
    parser.add_argument('--step-protocol', choices=('text', 'tool'), default='text',
                        help='Use native execute_python tool calls for coordinator steps; reuse the RLM sandbox and child calls')
    parser.add_argument('--diagnose-protocol', action='store_true', help='Record response shapes at provider and adapter boundaries, without response text')
    args = parser.parse_args()
    if not args.prepare_only and args.model_id is None:
        parser.error('--model-id is required unless --prepare-only is used')
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name: str, value: object) -> None:
        (args.output / name).write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')

    context = json.loads(args.context.read_text(encoding='utf-8'))
    save('context-summary.json', validate_context(context))
    tasks = json.loads(args.questions.read_text(encoding='utf-8'))
    if args.case:
        if set(args.case) - {t['id'] for t in tasks}:
            parser.error('Unknown case ID')
        tasks = [t for t in tasks if t['id'] in args.case]
    inventory = sql_inventory(context['files'])
    save('inventory.json', inventory)
    save('questions.json', tasks)
    catalogue = [{k: v for k, v in table.items() if k != 'declarations'} for table in inventory['tables'].values()]
    save('catalogue.json', catalogue)
    print(f"Prepared {len(catalogue)} schema/name candidates, {len({t['name'] for t in catalogue})} distinct table names.", flush=True)
    if args.prepare_only:
        save('status.json', {'status': 'prepared', 'model_calls': 0})
        return

    connection = saved_connection(repo, args.model_id)
    model = connection['model']
    if model['provider'] != 'openai' or model['api_format'] != 'responses':
        raise ValueError('This experiment uses a saved OpenAI-compatible Responses connection')
    dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
    options = dict(model=f"openai/{model['model']}", model_type='responses', api_base=model['base_url'],
                   api_key=connection.pop('key'), reasoning={'effort': args.reasoning_effort},
                   store=False, cache=False, num_retries=0, timeout=600)
    root, child = dspy.LM(**options), dspy.LM(**options, response_format={'type': 'json_object'})
    adapter = dspy.JSONAdapter() if args.adapter == 'json' else dspy.ChatAdapter()
    dspy.configure(lm=root, adapter=adapter)
    evidence = SourceEvidence(context, inventory)
    prompt_path = args.prompt or repo / 'docs/prompts' / (
        'rlm-state-analysis.md' if args.workflow == 'staged' else 'rlm-state-questions.md')
    prompt = prompt_path.read_text(encoding='utf-8')
    (args.output / 'instructions.md').write_text(prompt, encoding='utf-8')
    for filename in ('rlm_state.py', 'rlm_evidence.py', 'rlm_protocol.py', 'rlm_native.py', 'rlm_staged.py'):
        (args.output / filename).write_text((repo / 'scripts/benchmarks' / filename).read_text(encoding='utf-8'), encoding='utf-8')
    save('manifest.json', {'model': model, 'credential_source': 'LLM Settings',
        'adapter': type(adapter).__name__ if args.workflow == 'rlm' else None,
        'context_path': str(args.context.resolve()), 'context_sha256': hashlib.sha256(args.context.read_bytes()).hexdigest(),
        'source_hashes': [p['source_hash'] for p in context['projects']],
        'workflow': args.workflow,
        'max_iters_per_case': args.max_iters if args.workflow == 'rlm' else None,
        'max_subcalls_per_case': args.max_subcalls if args.workflow == 'rlm' else 1,
        'reasoning_effort': args.reasoning_effort, 'expected_answers_visible_to_model': False,
        'diagnose_protocol': args.diagnose_protocol,
        'requested_adapter': args.adapter if args.workflow == 'rlm' else None,
        'supports_response_schema': root.supports_response_schema,
        'step_protocol': args.step_protocol if args.workflow == 'rlm' else None,
        'versions': {p: importlib.metadata.version(p) for p in ('dspy', 'pglast', 'deno', 'litellm')}})

    results, usages = [], []
    for task in tasks:
        directory = args.output / task['id']
        directory.mkdir()
        questions = task['questions']

        def validate_answers(answers: list[dict]) -> dict:
            """Check question accounting and exact citation locations. Returns precise errors; no expected facts."""
            return check_answers(answers, questions, context['files'])

        before = {'root': len(root.history), 'child': len(child.history)}
        started = time.monotonic()
        print(f"Running {task['id']} ({len(questions)} questions)", flush=True)
        callbacks = [ExecutionLog(directory / 'execution.jsonl')]
        if args.diagnose_protocol:
            callbacks.append(ProtocolLog(directory / 'protocol.jsonl'))
        native_step = None
        try:
            if args.workflow == 'staged':
                record = run_staged(evidence, task, child, prompt, directory)
            else:
                analyzer = dspy.RLM(StateQuestions.with_instructions(prompt), sub_lm=child,
                    max_iters=args.max_iters, max_llm_calls=args.max_subcalls, max_output_chars=18000,
                    tools=[evidence.get_source_range, evidence.search_symbols, evidence.get_function,
                           evidence.get_callers, evidence.get_callees, evidence.get_sql_query,
                           evidence.get_table, validate_answers])
                with (OpenAI(api_key=options['api_key'], base_url=options['api_base'],
                             max_retries=0, timeout=600) if args.step_protocol == 'tool' else nullcontext()) as client, \
                        dspy.context(callbacks=callbacks):
                    if client is not None:
                        native_step = NativePythonStep(client, model['model'], args.reasoning_effort,
                                                       analyzer.generate_action.signature.instructions)
                        analyzer.generate_action = native_step
                        (directory / 'native-instructions.md').write_text(native_step.instructions, encoding='utf-8')
                        save(f"{task['id']}/native-tool.json", native_step.tool)
                    result = analyzer(service_context=context, catalogue=catalogue, task=task)
                checks = check_answers(result.answers, questions, context['files'])
                forced = result.final_reasoning == 'Extract forced final output'
                record = {'case': task['id'], 'status': 'completed' if not checks['errors'] else 'invalid',
                          'answers': result.answers, 'checks': checks, 'forced_extraction': forced,
                          'completion_path': 'fallback' if forced else 'rlm_submit',
                          'workflow_completed': not checks['errors'] and not forced
                              and len(child.history) > before['child']}
                save(f"{task['id']}/trajectory.json", [{'code': s['code'], 'output': s['output']} for s in result.trajectory])
        except Exception as exc:
            record = {'case': task['id'], 'status': 'failed', 'error_type': type(exc).__name__, 'answers': [],
                      'completion_path': 'failed', 'workflow_completed': False}
            if isinstance(exc, APIStatusError):
                body = exc.body if isinstance(exc.body, dict) else {}
                error = body['error'] if isinstance(body.get('error'), dict) else body
                record['http_status'] = exc.status_code
                record['provider_error'] = str(error.get('message', '')).replace(options['api_key'], '[redacted]')
            if isinstance(exc, dspy.utils.exceptions.AdapterParseError):
                record['format_error'] = {
                    'adapter': exc.adapter_name, 'expected_fields': list(exc.signature.output_fields),
                    'parsed_fields': list(exc.parsed_result or {}),
                    'response_characters': len(exc.lm_response),
                    'xml_tags': re.findall(r'</?([A-Za-z_][\w]*)\b', exc.lm_response),
                    'field_markers': re.findall(r'\[\[ ## (\w+) ## \]\]', exc.lm_response),
                    'json_keys': re.findall(r'"([a-z_]+)"\s*:', exc.lm_response),
                    'has_cdata': '<![CDATA[' in exc.lm_response,
                    'position': getattr(exc.__cause__, 'position', None),
                }
        record['seconds'] = time.monotonic() - started
        save(f"{task['id']}/result.json", record)
        results.append(record)
        calls = public_calls(root, 'root')[before['root']:] + public_calls(child, 'child')[before['child']:]
        if native_step is not None:
            calls = native_step.calls + calls
            save(f"{task['id']}/native-protocol.json", native_step.protocol)
        save(f"{task['id']}/calls.json", calls)
        if args.diagnose_protocol:
            save(f"{task['id']}/protocol-calls.json", protocol_calls(root.history[before['root']:]))
        usage = {'case': task['id'], 'calls': len(calls), 'child_calls': sum(c['role'] == 'child' for c in calls),
                 'input_tokens': sum(c['usage'].get('input_tokens', 0) for c in calls),
                 'cached_input_tokens': sum((c['usage'].get('input_tokens_details') or {}).get('cached_tokens', 0) or 0 for c in calls),
                 'output_tokens': sum(c['usage'].get('output_tokens', 0) for c in calls)}
        usages.append(usage)
        save('usage.json', usages)
        print(f"{task['id']}: {record['status']}", flush=True)

    # Expected facts are read only after all model calls; they never enter prompts/tools.
    expected = json.loads(args.expected.read_text(encoding='utf-8'))
    same_snapshot = {expected['snapshot_source_hash']} == {p['source_hash'] for p in context['projects']}
    answers = {a['id']: a for r in results for a in r['answers'] if isinstance(a, dict) and isinstance(a.get('id'), str)}
    fact_checks = [{'id': q['id'], 'expected': expected['answers'].get(q['id']),
                    'actual': answers.get(q['id'], {}).get('value'),
                    'correct': same_snapshot and q['id'] in expected['answers']
                        and answers.get(q['id'], {}).get('value') == expected['answers'][q['id']]}
                   for task in tasks for q in task['questions']]
    names = {t['name'] for t in catalogue}
    save('evaluation.json', {'snapshot_matches_fixture': same_snapshot,
        'missing_table_names': sorted(set(expected['table_names']) - names),
        'unexpected_table_names': sorted(names - set(expected['table_names'])),
        'fact_checks': fact_checks, 'correct': sum(c['correct'] for c in fact_checks), 'total': len(fact_checks),
        'note': 'Exact factual regression cases and quote checks; not a general semantic or completeness score. '
                'Table accounting is deterministic extraction, not an LLM achievement. No factual retry used the expected answers.'})
    save('status.json', {'status': 'completed' if all(r['workflow_completed'] for r in results) else 'incomplete',
                        'workflow_completed_cases': sum(r['workflow_completed'] for r in results),
                        'fallback_cases': sum(r.get('forced_extraction', False) for r in results),
                        'total_cases': len(results)})
    print(f"Exact facts: {sum(c['correct'] for c in fact_checks)}/{len(fact_checks)}", flush=True)


if __name__ == '__main__':
    main()
