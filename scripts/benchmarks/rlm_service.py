"""Load a whole service into DSPy's RLM and produce its first coverage inventory."""

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import dspy
from dspy.primitives.code_interpreter import FinalOutput
from dspy.utils.callback import BaseCallback

from rlm_handler import public_calls, saved_connection


class ServiceInventory(dspy.Signature):
    service_context: dict[str, Any] = dspy.InputField(desc="Whole service snapshot: metadata, projects, source files, complete saved graphs and explicit gaps. Inspect selectively with Python.")
    task: str = dspy.InputField(desc="The inventory objective; detailed behavior analysis is a later task.")
    inventory: dict[str, Any] = dspy.OutputField(desc="overview, apis, messaging, jobs, state and coverage, with exact source evidence.")
    report: str = dspy.OutputField(desc="Readable Markdown inventory including source references and unresolved gaps.")


class ExecutionLog(BaseCallback):
    def __init__(self, output: Path):
        self.output = output
        self.pending: dict[str, str] = {}

    def on_lm_start(self, call_id, instance, inputs):
        print('Model call started', flush=True)

    def on_lm_end(self, call_id, outputs, exception):
        print('Model call failed' if exception is not None else 'Model call completed', flush=True)

    def on_interpreter_execute_start(self, call_id, instance, inputs):
        self.pending[call_id] = inputs.get('code', '')

    def on_interpreter_execute_end(self, call_id, outputs, exception):
        code = self.pending.pop(call_id, '')
        if code:
            event = {'code': code, 'output': outputs.output if isinstance(outputs, FinalOutput) else outputs,
                     'error_type': type(exception).__name__ if exception is not None else None}
            with self.output.open('a', encoding='utf-8') as file:
                file.write(json.dumps(event, default=str) + '\n')


def validate_context(context: dict) -> dict:
    """Check snapshot consistency without making any model calls."""
    if context['schema_version'] != 1 or not context['files']:
        raise ValueError('Supply a version 1 service context containing source files')
    if set(context['files']) != set(context['file_manifest']):
        raise ValueError('Source files and manifest differ')
    prefixes = {str(p['id']) for p in context['projects']}
    if len(prefixes) != len(context['projects']) or prefixes != set(context['graphs']):
        raise ValueError('Each selected code project needs exactly one graph')
    for path, content in context['files'].items():
        parts = Path(path).parts
        if Path(path).is_absolute() or '..' in parts or parts[0] not in prefixes:
            raise ValueError('Source file key outside its project namespace')
        raw = content.encode('utf-8')
        if hashlib.sha256(raw).hexdigest() != context['file_manifest'][path]['sha256']:
            raise ValueError(f'Source hash mismatch: {path}')
        if len(raw) != context['file_manifest'][path]['bytes']:
            raise ValueError(f'Source length mismatch: {path}')
    for prefix, graph in context['graphs'].items():
        ids = {node['id'] for node in graph['nodes']}
        if len(ids) != len(graph['nodes']):
            raise ValueError('Duplicate graph node IDs within one project')
        if len(ids) != graph['stats']['nodeCount'] or len(graph['edges']) != graph['stats']['edgeCount']:
            raise ValueError('Incomplete graph export')
        for node in graph['nodes']:
            if node['file_key'] != f"{prefix}/{node['filePath']}":
                raise ValueError('Node source key differs from its indexed path')
        for edge in graph['edges']:
            if edge['source'] not in ids or edge['target'] not in ids:
                raise ValueError('Graph edge endpoint is missing from the export')
        for indexed in graph['files']:
            key = indexed['file_key']
            if key in context['file_manifest'] and indexed['contentHash'] != context['file_manifest'][key]['sha256']:
                raise ValueError('Graph and source hashes differ')
    return {'service': context['service']['name'], 'projects': len(prefixes),
            'files': len(context['files']), 'source_characters': sum(map(len, context['files'].values())),
            'nodes': sum(len(g['nodes']) for g in context['graphs'].values()),
            'edges': sum(len(g['edges']) for g in context['graphs'].values()),
            'unresolved_references': sum(len(g['unresolved_references']) for g in context['graphs'].values()),
            'omissions': context['omissions']}


def check_inventory(inventory: dict, files: dict[str, str]) -> dict:
    """Check output structure and citations; this is not a semantic evaluator."""
    errors, citations = [], []
    for section in ('apis', 'messaging', 'jobs', 'state'):
        entries = inventory.get(section)
        if not isinstance(entries, list):
            errors.append(f'{section}: expected a list')
            continue
        for index, entry in enumerate(entries):
            name = f'{section}[{index}]'
            if not isinstance(entry, dict):
                errors.append(f'{name}: expected an object')
                continue
            if any(not isinstance(entry.get(k), str) or not entry[k] for k in ('name', 'description')):
                errors.append(f'{name}: missing name/description')
            if entry.get('origin') not in ('runtime', 'test', 'configuration', 'unverified'):
                errors.append(f'{name}: missing/invalid origin')
            evidence = entry.get('evidence')
            if not isinstance(evidence, list) or not evidence:
                errors.append(f'{name}: missing evidence list')
                continue
            for citation in evidence:
                if not isinstance(citation, dict):
                    errors.append(f'{name}: invalid citation')
                    continue
                start, end, quote = citation.get('start_line'), citation.get('end_line'), citation.get('quote')
                path = citation.get('path')
                lines = files.get(path, '').splitlines() if isinstance(path, str) else []
                valid = type(start) is int and type(end) is int and 1 <= start <= end <= len(lines)
                matched = valid and isinstance(quote, str) and bool(quote.strip()) and quote.strip() in '\n'.join(lines[start - 1:end])
                citations.append({'entry': name, 'path': path, 'start_line': start, 'end_line': end, 'matches': bool(matched)})
    coverage = inventory.get('coverage')
    if not isinstance(coverage, dict):
        errors.append('Missing coverage object')
    else:
        for field in ('inspected_files', 'uninspected_candidates', 'gaps'):
            if not isinstance(coverage.get(field), list):
                errors.append(f'coverage.{field}: expected a list')
        for path in coverage.get('inspected_files', []) if isinstance(coverage.get('inspected_files'), list) else []:
            if not isinstance(path, str) or path not in files:
                errors.append('coverage.inspected_files contains an unavailable file')
    if not isinstance(inventory.get('overview'), str):
        errors.append('Missing overview')
    return {'structure_errors': errors, 'citations': citations,
            'matching_citations': sum(c['matches'] for c in citations), 'citation_count': len(citations),
            'semantic_correctness': 'not evaluated', 'coverage_completeness': 'not established'}


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--service-id', help='Export all active code projects linked to this saved service')
    source.add_argument('--context', type=Path, help='Reuse an exported immutable context.json')
    parser.add_argument('--model-id')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--prompt', type=Path, default=repo / 'docs/prompts/rlm-service-inventory.md')
    parser.add_argument('--task', default='Build the first whole-service inventory of runtime entry points, background work and managed state. Keep unknowns and remaining coverage explicit.')
    parser.add_argument('--max-iters', type=int, default=20)
    parser.add_argument('--max-subcalls', type=int, default=8)
    parser.add_argument('--reasoning-effort', default='max')
    args = parser.parse_args()
    if not args.prepare_only and args.model_id is None:
        parser.error('--model-id is required unless --prepare-only is used')
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name: str, value: Any) -> None:
        (args.output / name).write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')

    if args.service_id is not None:
        exported = subprocess.run(['docker', 'compose', '--env-file', '.env', '-f', 'deploy/compose.yaml',
            'exec', '-T', 'admin-api', '/services/admin-api/.venv/bin/python', '-', args.service_id],
            cwd=repo, input=(repo / 'scripts/benchmarks/export_service_context.py').read_text(encoding='utf-8'),
            text=True, capture_output=True, check=False, timeout=300)
        if exported.returncode != 0:
            raise RuntimeError('Service export failed; check the linked projects and generate fresh graphs. Source output suppressed.')
        context = json.loads(exported.stdout)
    else:
        context = json.loads(args.context.read_text(encoding='utf-8'))
    summary = validate_context(context)
    save('context.json', context)
    save('context-summary.json', summary)
    print(json.dumps(summary), flush=True)
    if args.prepare_only:
        save('status.json', {'status': 'prepared', 'model_calls': 0})
        return

    connection = saved_connection(repo, args.model_id)
    model = connection['model']
    if model['provider'] != 'openai' or model['api_format'] != 'responses':
        raise ValueError('Select an OpenAI-compatible Responses model from LLM Settings')
    dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
    options = dict(model=f"openai/{model['model']}", model_type='responses',
                   api_base=model['base_url'], api_key=connection.pop('key'),
                   reasoning={'effort': args.reasoning_effort},
                   store=False, cache=False, num_retries=0, timeout=600)
    root, child = dspy.LM(**options), dspy.LM(**options, response_format={'type': 'json_object'})
    dspy.configure(lm=root, adapter=dspy.XMLAdapter(), callbacks=[ExecutionLog(args.output / 'execution.jsonl')])
    prompt = args.prompt.read_text(encoding='utf-8')
    analyzer = dspy.RLM(ServiceInventory.with_instructions(prompt), sub_lm=child,
                        max_iters=args.max_iters, max_llm_calls=args.max_subcalls, max_output_chars=12000)
    (args.output / 'instructions.md').write_text(prompt, encoding='utf-8')
    (args.output / 'runner.py').write_text(Path(__file__).read_text(encoding='utf-8'), encoding='utf-8')
    save('manifest.json', {'model': model, 'task': args.task, 'reasoning_effort': args.reasoning_effort,
                          'adapter': 'XMLAdapter', 'child_response_format': 'json_object',
                          'max_iters': args.max_iters, 'max_subcalls': args.max_subcalls,
                          'context_sha256': hashlib.sha256((args.output / 'context.json').read_bytes()).hexdigest(),
                          'credential_source': 'LLM Settings', 'versions': {
                              p: importlib.metadata.version(p) for p in ('dspy', 'deno', 'litellm', 'openai')}})
    started = time.monotonic()
    try:
        result = analyzer(service_context=context, task=args.task)
        save('inventory.json', result.inventory)
        (args.output / 'inventory.md').write_text(result.report, encoding='utf-8')
        save('checks.json', check_inventory(result.inventory, context['files']))
        save('trajectory.json', [{'code': s['code'], 'output': s['output']} for s in result.trajectory])
        save('status.json', {'status': 'completed', 'seconds': time.monotonic() - started,
                             'forced_extraction': result.final_reasoning == 'Extract forced final output'})
        print('Inventory saved for review.', flush=True)
    except Exception as exc:
        save('status.json', {'status': 'failed', 'error_type': type(exc).__name__, 'seconds': time.monotonic() - started})
        print(f'Inventory failed: {type(exc).__name__}. Details suppressed; inspect execution.jsonl.', flush=True)
        raise SystemExit(1) from None
    finally:
        calls = public_calls(root, 'root') + public_calls(child, 'child')
        save('calls.json', calls)
        save('usage.json', {role: {'calls': sum(c['role'] == role for c in calls),
            'input_tokens': sum(c['usage'].get('input_tokens', 0) for c in calls if c['role'] == role),
            'cached_input_tokens': sum((c['usage'].get('input_tokens_details') or {}).get('cached_tokens', 0) or 0 for c in calls if c['role'] == role),
            'output_tokens': sum(c['usage'].get('output_tokens', 0) for c in calls if c['role'] == role)}
            for role in ('root', 'child')})


if __name__ == '__main__':
    main()
