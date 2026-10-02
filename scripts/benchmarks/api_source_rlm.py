"""Analyse a frozen API with source-only RLM discovery, without graph evidence."""

import argparse
import hashlib
import importlib.metadata
import json
import time
from collections import defaultdict
from pathlib import Path
from threading import Lock
from typing import Any

import dspy
from openai import OpenAI

from rlm_evidence import check_answers
from rlm_handler import public_calls, saved_connection
from rlm_native import NativePythonStep
from rlm_service import ExecutionLog, validate_context


class SourceEndpoint(dspy.Signature):
    sources: dict[str, str] = dspy.InputField(desc='Frozen source files, keyed by project-relative path. Search and slice in Python; use splitlines() for citations.')
    task: dict[str, Any] = dspy.InputField(desc='Endpoint, initial route/handler locations and exact questions. No dependency graph or expected answers.')
    answers: list[dict[str, Any]] = dspy.OutputField(desc='One {id,value,explanation,evidence:[{path,start_line,end_line,quote}]} per supplied question.')


class ReplayRlmSteps(dspy.Module):
    """Restore successful workspace operations before retrying a failed model call."""

    def __init__(self, native: NativePythonStep, codes: list[str]):
        super().__init__()
        self.native = native
        self.codes = codes
        self.position = 0
        self.active = False

    def forward(self, **kwargs: Any) -> dspy.Prediction:
        if self.position < len(self.codes):
            self.active = True
            code = self.codes[self.position]
            self.position += 1
            print(f'Replaying workspace step {self.position}/{len(self.codes)} without a model call', flush=True)
            return dspy.Prediction(reasoning='', code=code)
        self.active = False
        return self.native(**kwargs)


def prepare_source_task(context: dict, selection: dict) -> dict:
    """Allow only original source and endpoint seeds across the model boundary."""
    project = next(p for p in context['projects'] if p['id'] == selection['project_id'])
    if project['source_hash'] != selection['snapshot_source_hash']:
        raise ValueError('Selection belongs to another source snapshot')
    sources = {path: text for path, text in context['files'].items()
               if path.startswith(selection['project_id'] + '/')}
    if not sources:
        raise ValueError('Selected project has no source files')
    inputs = {'sources': sources, 'task': {
        'endpoint': selection['endpoint'], 'project_id': selection['project_id'],
        'handler': selection['handler'], 'middleware': selection['middleware'],
        'route_ranges': selection['route_ranges'],
        'questions': [*selection['handler_questions'], *selection['synthesis_questions']],
    }}
    if 'scope' in selection:
        if not isinstance(selection['scope'], str) or not selection['scope'].strip():
            raise ValueError('Analysis scope must be nonempty text')
        inputs['task']['scope'] = selection['scope']
    return inputs


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--model-id')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prompt', type=Path, default=repo / 'docs/prompts/api-source-rlm.md')
    parser.add_argument('--max-iters', type=int, default=30)
    parser.add_argument('--max-subcalls', type=int, default=16)
    parser.add_argument('--reasoning-effort', default='max')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume-from', type=Path,
                        help='Restore successful workspace steps, reusing saved child responses only for identical prompts')
    args = parser.parse_args()
    if not args.prepare_only and args.model_id is None:
        parser.error('--model-id is required unless --prepare-only is used')
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name: str, value: object) -> None:
        (args.output / name).write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')

    context = json.loads(args.context.read_text(encoding='utf-8'))
    validate_context(context)  # Consistency check only; graphs do not enter the RLM.
    selection = json.loads(args.selection.read_text(encoding='utf-8'))
    inputs = prepare_source_task(context, selection)
    del context
    save('model-input.json', inputs)
    save('status.json', {'status': 'prepared', 'source_files': len(inputs['sources']), 'graphs_exposed': False})
    if args.prepare_only:
        return

    connection = saved_connection(repo, args.model_id)
    model = connection['model']
    if model['provider'] != 'openai' or model['api_format'] != 'responses':
        raise ValueError('Select an OpenAI-compatible Responses model from LLM Settings')
    key = connection.pop('key')
    dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
    options = dict(model=f"openai/{model['model']}", model_type='responses',
                   api_base=model['base_url'], api_key=key, reasoning={'effort': args.reasoning_effort},
                   store=False, cache=False, num_retries=0, timeout=600)
    root = dspy.LM(**options)
    child = dspy.LM(**options, response_format={'type': 'json_object'})
    dspy.configure(lm=root, adapter=dspy.ChatAdapter())
    prompt = args.prompt.read_text(encoding='utf-8')
    replay_codes = []
    replay_children = defaultdict(list)
    if args.resume_from is not None:
        previous = args.resume_from
        prior_manifest = json.loads((previous / 'manifest.json').read_text(encoding='utf-8'))
        current_input_hash = hashlib.sha256((args.output / 'model-input.json').read_bytes()).hexdigest()
        if (prior_manifest['model'] != model or prior_manifest['model_input_sha256'] != current_input_hash
                or prior_manifest['reasoning_effort'] != args.reasoning_effort
                or prior_manifest['max_iters'] != args.max_iters or prior_manifest['max_subcalls'] != args.max_subcalls
                or (previous / 'instructions.md').read_text(encoding='utf-8') != prompt):
            raise ValueError('Replay requires identical source, task, model, prompt and run limits')
        if json.loads((previous / 'status.json').read_text(encoding='utf-8'))['status'] != 'failed':
            raise ValueError('Resume requires a failed run')
        events = [json.loads(line) for line in (previous / 'execution.jsonl').read_text(encoding='utf-8').splitlines()]
        if any(e['error_type'] is not None or 'SUBMIT(' in e['code'] for e in events):
            raise ValueError('Replay requires successful workspace steps before submission')
        replay_codes = [e['code'] for e in events]
        for call in json.loads((previous / 'calls.json').read_text(encoding='utf-8')):
            if call['role'] != 'child':
                continue
            if (len(call['messages']) != 1 or call['messages'][0]['role'] != 'user'
                    or len(call['answers']) != 1 or not isinstance(call['answers'][0], str)):
                raise ValueError('Cannot replay this child response shape')
            replay_children[call['messages'][0]['content']].append(call['answers'][0])
    (args.output / 'instructions.md').write_text(prompt, encoding='utf-8')
    for name in ('api_source_rlm.py', 'rlm_native.py', 'rlm_evidence.py', 'rlm_handler.py', 'rlm_service.py'):
        (args.output / name).write_text((Path(__file__).parent / name).read_text(encoding='utf-8'), encoding='utf-8')
    save('manifest.json', {'model': model, 'credential_source': 'LLM Settings',
        'context_path': str(args.context.resolve()),
        'context_sha256': hashlib.sha256(args.context.read_bytes()).hexdigest(),
        'model_input_sha256': hashlib.sha256((args.output / 'model-input.json').read_bytes()).hexdigest(),
        'source_hash': selection['snapshot_source_hash'], 'source_files': len(inputs['sources']),
        'source_characters': sum(map(len, inputs['sources'].values())),
        'graphs_exposed': False, 'expected_answers_visible_to_model': False,
        'workflow': 'source-only autonomous RLM with native Python steps',
        'replayed_from': str(args.resume_from.resolve()) if args.resume_from is not None else None,
        'replayed_inspection_steps': len(replay_codes),
        'max_iters': args.max_iters, 'max_subcalls': args.max_subcalls,
        'reasoning_effort': args.reasoning_effort, 'max_output_chars': 18000,
        'versions': {p: importlib.metadata.version(p) for p in ('dspy', 'deno', 'litellm', 'openai')}})

    def validate_answers(answers: list[dict]) -> dict:
        """Check all required answers and exact source quotations; no expected facts are provided."""
        return check_answers(answers, inputs['task']['questions'], inputs['sources'])

    replay = None
    replay_lock = Lock()

    def query_child(prompt: str) -> list[str]:
        if replay is not None and replay.active:
            with replay_lock:
                if not replay_children.get(prompt):
                    raise ValueError('Replayed child prompt has no exact saved response')
                return [replay_children[prompt].pop(0)]
        return child(prompt)

    analyzer = dspy.RLM(SourceEndpoint.with_instructions(prompt), sub_lm=query_child if replay_codes else child,
                        max_iters=args.max_iters, max_llm_calls=args.max_subcalls,
                        max_output_chars=18000, tools=[validate_answers])
    started = time.monotonic()
    record = {'status': 'running', 'answers': []}
    save('status.json', {'status': 'running'})
    native = None
    try:
        with OpenAI(api_key=key, base_url=model['base_url'], max_retries=0, timeout=600) as client, \
                dspy.context(callbacks=[ExecutionLog(args.output / 'execution.jsonl')]):
            native = NativePythonStep(client, model['model'], args.reasoning_effort,
                                      analyzer.generate_action.signature.instructions)
            replay = ReplayRlmSteps(native, replay_codes) if replay_codes else None
            analyzer.generate_action = replay if replay is not None else native
            (args.output / 'native-instructions.md').write_text(native.instructions, encoding='utf-8')
            result = analyzer(**inputs)
        checks = validate_answers(result.answers)
        forced = result.final_reasoning == 'Extract forced final output'
        record = {'status': 'completed' if not checks['errors'] and not forced else 'incomplete',
                  'answers': result.answers, 'checks': checks, 'forced_extraction': forced}
        save('trajectory.json', [{'code': step['code'], 'output': step['output']} for step in result.trajectory])
        save('validation.json', checks)
    except Exception as exc:
        record = {'status': 'failed', 'error_type': type(exc).__name__, 'answers': []}
        print(f'Run failed: {type(exc).__name__}. See saved execution/protocol logs.', flush=True)
    finally:
        record['seconds'] = time.monotonic() - started
        save('result.json', record)
        save('status.json', {k: v for k, v in record.items() if k not in ('answers', 'checks')})
        calls = (native.calls if native is not None else []) + public_calls(root, 'root') + public_calls(child, 'child')
        save('calls.json', calls)
        if native is not None:
            save('native-protocol.json', native.protocol)
        usage = {'calls': len(calls), 'child_calls': sum(c['role'] == 'child' for c in calls),
            'input_tokens': sum(c['usage'].get('input_tokens', 0) for c in calls),
            'cached_input_tokens': sum((c['usage'].get('input_tokens_details') or {}).get('cached_tokens', 0) or 0 for c in calls),
            'output_tokens': sum(c['usage'].get('output_tokens', 0) for c in calls),
            'reasoning_tokens': sum((c['usage'].get('output_tokens_details') or {}).get('reasoning_tokens', 0) or 0 for c in calls)}
        usage['total_tokens'] = usage['input_tokens'] + usage['output_tokens']
        save('usage.json', usage)
        print(json.dumps({'status': record['status'], **usage}), flush=True)

    # Read the answer key only after model execution has finished.
    expected = json.loads(args.expected.read_text(encoding='utf-8'))
    if expected['snapshot_source_hash'] != selection['snapshot_source_hash']:
        raise ValueError('Expected answers belong to another snapshot')
    answers = {a['id']: a for a in record['answers']}
    question_ids = {q['id'] for q in inputs['task']['questions']}
    checks = [{'id': qid, 'expected': value, 'actual': answers.get(qid, {}).get('value'),
               'correct': answers.get(qid, {}).get('value') == value}
              for qid, value in expected['answers'].items() if qid in question_ids]
    save('evaluation.json', {'checks': checks, 'correct': sum(c['correct'] for c in checks),
                            'total': len(checks), 'note': 'Targeted values only; full narrative requires source review.'})
    document = [f"# {selection['endpoint']}", '', 'Source-only RLM experiment. Model findings require review.', '']
    for answer in record['answers']:
        document.extend([f"## {answer['id']}", '', f"Value: {answer['value']}", '', answer['explanation'], ''])
        document.extend(f"- `{c['path']}:{c['start_line']}-{c['end_line']}`: {c['quote']}"
                        for c in answer['evidence'])
        document.append('')
    (args.output / 'document.md').write_text('\n'.join(document), encoding='utf-8')
    if 'api.flow' in answers:
        (args.output / 'overview.md').write_text(answers['api.flow']['explanation'], encoding='utf-8')
    if record['status'] != 'completed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
