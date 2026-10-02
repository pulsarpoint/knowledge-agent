"""Analyze one frozen Go API as separate source-backed function steps, then synthesize."""

import argparse
import copy
import hashlib
import json
import time
from collections import defaultdict, deque
from pathlib import Path

import dspy

from rlm_evidence import SourceEvidence, sql_inventory
from rlm_handler import public_calls, saved_connection
from rlm_service import validate_context
from rlm_staged import run_staged


def prepare_endpoint(context: dict, selection: dict) -> tuple[dict, dict]:
    """Follow saved calls; keep target candidates, unresolved calls and explicit middleware seeds."""
    pid = selection['project_id']
    graph = context['graphs'][pid]
    if graph['source_hash'] != selection['snapshot_source_hash']:
        raise ValueError('Selection belongs to another source snapshot')
    evidence = SourceEvidence(context, sql_inventory(context['files']))
    nodes = {n['id']: n for n in graph['nodes']}
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for edge in graph['edges']:
        outgoing[edge['source']].append(edge)
        if edge['kind'] == 'calls':
            incoming[edge['target']].append(edge)
    roots = []
    for seed in [selection['handler'], *selection['middleware']]:
        matches = evidence.search_symbols(seed['name'], f"{pid}/{seed['path']}")
        if len(matches) != 1:
            raise ValueError('Endpoint seed is missing or ambiguous')
        roots.append(matches[0]['id'].split('/', 1)[1])
    paths = {root: [root] for root in roots}
    queue = deque(roots)
    while queue:
        current = queue.popleft()
        for edge in outgoing[current]:
            target = edge['target']
            if edge['kind'] == 'calls' and target not in paths:
                paths[target] = [*paths[current], target]
                queue.append(target)
    application = [node for node in paths if nodes[node]['kind'] in ('function', 'method')
                   and not nodes[node]['filePath'].endswith(('queries.sql.go', '/db.go'))]
    # Stable IDs keep per-step results inspectable and rerunnable.
    ids = {node: f"{i:02d}-{nodes[node]['name']}" for i, node in enumerate(application, 1)}

    def fragment(path: str, start: int, end: int, kind: str) -> dict:
        key = f'{path}:{start}-{end}'
        evidence.get_source_range(path, start, end)  # Validate against the frozen source.
        item = {'id': key, 'path': path, 'start_line': start, 'end_line': end, 'kind': kind,
                'lines': [{'line': n, 'text': line} for n, line in
                          enumerate(context['files'][path].splitlines()[start - 1:end], start)]}
        return item

    route = [fragment(f"{pid}/{r['path']}", r['start_line'], r['end_line'], 'explicit route selection')
             for r in selection['route_ranges']]
    bundles = {}
    for node_id in application:
        node = nodes[node_id]
        verified = evidence.get_function(f'{pid}/{node_id}')
        fragments = {f['id']: f for f in route} if node_id in roots else {}
        own = fragment(verified['path'], verified['start_line'], verified['end_line'], 'AST-verified function')
        fragments[own['id']] = own
        # Supporting declarations and generated SQL stay with the owning function.
        support, todo = set(), [node_id]
        while todo:
            current = todo.pop()
            for edge in outgoing[current]:
                target = edge['target']
                if target in support or target in ids or edge['kind'] not in ('calls', 'references', 'instantiates'):
                    continue
                n = nodes[target]
                if n['kind'] in ('file', 'module') or not n.get('source_available'):
                    continue
                if n['kind'] in ('function', 'method') and not n['filePath'].endswith(('queries.sql.go', '/db.go')):
                    continue  # A non-call reference is not a reason to follow another function.
                support.add(target)
                todo.append(target)
                f = fragment(n['file_key'], n['startLine'], n['endLine'], 'graph supporting declaration')
                fragments[f['id']] = f
        for path in sorted({f['path'] for f in fragments.values()}):
            if not path.endswith('.go'):
                continue
            for match in evidence.scan_go(path):
                if match['ruleId'] == 'import':
                    f = fragment(path, match['range']['start']['line'] + 1,
                                 match['range']['end']['line'] + 1, 'imports')
                    fragments[f['id']] = f
        call_contexts, guard_contexts = [], {}
        for edge in incoming[node_id]:
            if edge['source'] not in paths:
                continue
            caller = nodes[edge['source']]
            line = edge.get('line')
            if not isinstance(line, int):
                continue
            scans = evidence.scan_go(caller['file_key'])
            calls = [m for m in scans if m['ruleId'] == 'call'
                     and m['range']['start']['line'] + 1 == line
                     and m['metaVariables']['single']['CALLEE']['text'] == edge.get('metadata', {}).get('refName')]
            guards = []
            for call in calls:
                begin, end = call['range']['byteOffset']['start'], call['range']['byteOffset']['end']
                for parent in scans:
                    if parent['ruleId'] not in ('if', 'defer_statement', 'go_statement', 'func_literal',
                                               'for_statement', 'expression_switch_statement', 'expression_case', 'default_case'):
                        continue
                    bounds = parent['range']['byteOffset']
                    if not bounds['start'] <= begin < end <= bounds['end']:
                        continue
                    guard = {'kind': parent['ruleId'], 'line': parent['range']['start']['line'] + 1,
                             'header': parent['text'].splitlines()[0]}
                    if parent['ruleId'] == 'if':
                        variables = parent['metaVariables']['single']
                        condition, then = variables['CONDITION'], variables['THEN']
                        guard['condition'] = condition['text']
                        del guard['header']
                        guard['position'] = ('condition' if condition['range']['byteOffset']['start'] <= begin < condition['range']['byteOffset']['end']
                                             else 'consequence' if then['range']['byteOffset']['start'] <= begin < then['range']['byteOffset']['end']
                                             else 'initializer or alternative; inspect caller')
                    guard_id = f"{caller['file_key']}:{guard['line']}:{guard['kind']}:{guard.get('position', '')}"
                    guard_contexts[guard_id] = guard
                    guards.append(guard_id)
            call_end = max((m['range']['end']['line'] + 1 for m in calls), default=line)
            excerpt = fragment(caller['file_key'], line, call_end, 'incoming call arguments')
            fragments[excerpt['id']] = excerpt
            call_contexts.append({'caller': caller['qualifiedName'], 'caller_step': ids.get(edge['source']),
                                  'line': line, 'resolution': edge.get('metadata'),
                                  'guards': sorted(guards), 'source_fragment': excerpt['id']})
        local_edges = [e for e in outgoing[node_id] if e['kind'] == 'calls']
        unresolved = defaultdict(list)
        for r in graph['unresolved_references']:
            if r['fromNodeId'] == node_id and r['referenceKind'] == 'calls':
                unresolved[r['referenceName']].append(r['line'])
        questions = [
            {'id': 'contract', 'question': 'What inputs, fields, conversions, return values and API caller context does this function use?'},
            {'id': 'decisions', 'question': 'What consequential predicates, early returns and mutually exclusive branches occur here? Keep nil/false/zero and call ordering precise.'},
            {'id': 'state', 'question': 'What database/state/external effects are visible locally or in supporting SQL? Give tables, conditions and transaction/error boundaries; separate effects owned by other functions.'},
            {'id': 'boundaries', 'question': 'What behavior depends on separately analyzed functions or unavailable external code? State unresolved dispatch, callback and error-response details without guessing.'},
        ]
        for q in questions:
            q['allowed_values'] = ['described', 'unknown']
        if node_id == roots[0]:
            questions.extend(selection['handler_questions'])
        task = {'id': ids[node_id], 'endpoint': selection['endpoint'], 'function': node,
                'questions': questions, 'resources': [], 'mode': 'function'}
        observations = {'call_context': {'incoming': call_contexts, 'guards': guard_contexts,
            'candidate_path': [{'node': n, 'name': nodes[n]['qualifiedName'], 'file': nodes[n]['filePath']} for n in paths[node_id]],
            'outgoing': [{'line': e.get('line'), 'resolution': e.get('metadata'), 'target': nodes[e['target']]['qualifiedName'],
                          'target_file': nodes[e['target']]['filePath'], 'analysis_step': ids.get(e['target'])} for e in local_edges],
            'noncall_function_references': [{'edge': e, 'target': nodes[e['target']]['qualifiedName'],
                                            'target_file': nodes[e['target']]['filePath']}
                                           for e in outgoing[node_id] if e['kind'] != 'calls'
                                           and nodes[e['target']]['kind'] in ('function', 'method')],
            'unresolved_calls': [{'expression': expression, 'lines': sorted(set(lines))} for expression, lines in unresolved.items()],
            'note': 'Graph candidate path and syntactic ancestry, not runtime reachability. Earlier returns and aliases require source review.'}}
        bundles[ids[node_id]] = {'task': task, 'fragments': list(fragments.values()), 'observations': observations,
                                'gaps': ['External dependency implementations are not included.'],
                                'snapshot_omissions': context.get('omissions', [])}
    plan = {'endpoint': selection['endpoint'], 'handler_step': ids[roots[0]], 'steps': list(bundles),
            'function_nodes': len(application), 'reachable_nodes': len(paths),
            'support_only_nodes': [nodes[n] for n in paths if n not in ids],
            'source_hash': graph['source_hash'], 'selection': selection,
            'note': 'Middleware seeds and route ranges are explicit selections; graph resolution and data-type closure can be incomplete.'}
    return plan, bundles


def evidence_identity(bundle: dict) -> dict:
    """Ignore only the ordering of syntactic ancestor IDs from parallel AST scans."""
    normalized = copy.deepcopy(bundle)
    for caller in normalized['observations'].get('call_context', {}).get('incoming', []):
        caller['guards'].sort()
    return normalized


def prepare_synthesis(selection: dict, bundles: dict, results: dict, handler_step: str) -> dict:
    """Keep findings intact while storing each cited source line and path once."""
    files = {}

    def add_source(source: dict, start: int, end: int) -> None:
        selected = {line['line']: line['text'] for line in source['lines'] if start <= line['line'] <= end}
        if set(selected) != set(range(start, end + 1)):
            raise ValueError('Synthesis citation includes unavailable source lines')
        lines = files.setdefault(source['path'], {})
        for number, text in selected.items():
            if number in lines and lines[number] != text:
                raise ValueError('Conflicting source text in synthesis evidence')
            lines[number] = text

    # Preserve the whole entry point and its route/types/imports, even if the
    # function analysis did not cite them. Uncited incoming callers are irrelevant.
    for source in bundles[handler_step]['fragments']:
        if source.get('kind') != 'incoming call arguments':
            add_source(source, source['start_line'], source['end_line'])
    for node_step, result in results.items():
        sources = {f['id']: f for f in bundles[node_step]['fragments']}
        for answer in result['answers']:
            for citation in answer['evidence']:
                add_source(sources[citation['fragment_id']], citation['start_line'], citation['end_line'])

    # Short IDs identify files, not symbols: equal names in different projects
    # remain distinct. Gaps stay absent and cannot be cited by the validator.
    file_ids = {path: f'S{i}' for i, path in enumerate(sorted(files), 1)}
    fragments = [{'id': file_ids[path], 'path': path, 'start_line': min(lines), 'end_line': max(lines),
                  'lines': [{'line': n, 'text': lines[n]} for n in sorted(lines)]}
                 for path, lines in sorted(files.items())]
    observations = {}
    for node_step, result in results.items():
        node = bundles[node_step]['task']['function']
        sources = {f['id']: f for f in bundles[node_step]['fragments']}
        answers = []
        for answer in result['answers']:
            citations = []
            for citation in answer['evidence']:
                item = {'fragment_id': file_ids[sources[citation['fragment_id']]['path']],
                        'start_line': citation['start_line'], 'end_line': citation['end_line']}
                if item not in citations:
                    citations.append(item)
            # Retain every finding, including unknowns and conflicting interpretations.
            # Original graph observation IDs belong to the saved node bundle and
            # do not resolve in this synthesis bundle. Source citations do.
            answers.append({**{k: v for k, v in answer.items() if k not in ('evidence', 'observation_ids')},
                            'evidence': citations})
        observations[node_step] = {
            'function': {k: node[k] for k in ('qualifiedName', 'signature', 'startLine', 'endLine') if k in node},
            'source_id': file_ids.get(node.get('file_key')),
            'answers': answers, 'needs_evidence': result.get('needs_evidence', []),
        }
    task = {'id': 'synthesis', 'endpoint': selection['endpoint'], 'questions': selection['synthesis_questions'],
            'resources': [], 'mode': 'synthesis'}
    return {'task': task, 'fragments': fragments, 'observations': observations,
            'gaps': ['Node findings are unverified interpretations; source and unresolved boundaries remain authoritative.',
                     'Source excerpts contain gaps. Cite only contiguous lines actually shown, not an entire file span.',
                     'Graph metadata and uncited caller context remain in saved function analyses, not this bundle.',
                     'Attribute each effect to its source function; calling a transactional helper does not give its caller a transaction.']}


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--selection', type=Path, default=repo / 'scripts/benchmarks/fixtures/api_staged_personal_limits.json')
    parser.add_argument('--expected', type=Path, default=repo / 'scripts/benchmarks/fixtures/api_staged_personal_limits_expected.json')
    parser.add_argument('--prompt', type=Path, default=repo / 'docs/prompts/api-node-staged.md')
    parser.add_argument('--model-id')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--step', action='append', help='Run only selected step IDs; full runs also synthesize')
    parser.add_argument('--resume-from', type=Path, help='Revalidate saved responses with identical evidence, prompt and model')
    parser.add_argument('--rerun-step', action='append', default=[], help='Explicitly request a new call instead of reusing this step')
    parser.add_argument('--repair-step', action='append', default=[], help='Ask for a new answer using only saved structural validation errors')
    args = parser.parse_args()
    if not args.prepare_only and args.model_id is None:
        parser.error('--model-id required for model calls')
    if args.repair_step and args.resume_from is None:
        parser.error('--repair-step requires --resume-from')
    if set(args.repair_step) & set(args.rerun_step):
        parser.error('Choose either repair or rerun for each step')
    args.output.mkdir(parents=True, exist_ok=False)

    def save(name: str, value: object) -> None:
        (args.output / name).write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')

    context = json.loads(args.context.read_text(encoding='utf-8'))
    save('context-checks.json', validate_context(context))
    selection = json.loads(args.selection.read_text(encoding='utf-8'))
    plan, bundles = prepare_endpoint(context, selection)
    save('plan.json', plan)
    for step, bundle in bundles.items():
        (args.output / step).mkdir()
        save(f'{step}/evidence.json', bundle)
    print(f"Prepared {len(bundles)} function steps; {plan['reachable_nodes']} reachable graph nodes", flush=True)
    if args.prepare_only:
        save('status.json', {'status': 'prepared', 'model_calls': 0})
        return
    if args.step and set(args.step) - bundles.keys():
        parser.error('Unknown step ID; inspect prepare-only plan')
    if set(args.repair_step + args.rerun_step) - (bundles.keys() | {'synthesis'}):
        parser.error('Unknown repair/rerun step ID')
    connection = saved_connection(repo, args.model_id)
    model = connection['model']
    if model['provider'] != 'openai' or model['api_format'] != 'responses':
        raise ValueError('Requires a saved OpenAI-compatible Responses model')
    dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
    child = dspy.LM(model=f"openai/{model['model']}", model_type='responses', api_base=model['base_url'],
                    api_key=connection.pop('key'), reasoning={'effort': 'max'}, response_format={'type': 'json_object'},
                    store=False, cache=False, num_retries=0, timeout=600)
    prompt = args.prompt.read_text(encoding='utf-8')
    if args.resume_from is not None:
        previous = json.loads((args.resume_from / 'manifest.json').read_text(encoding='utf-8'))
        if (previous['model'] != model or previous['reasoning_effort'] != 'max'
            or previous['context_sha256'] != hashlib.sha256(args.context.read_bytes()).hexdigest()
            or (args.resume_from / 'instructions.md').read_text(encoding='utf-8') != prompt):
            raise ValueError('Cannot reuse responses from different model settings, source or prompt')
    (args.output / 'instructions.md').write_text(prompt, encoding='utf-8')
    for filename in ('api_staged.py', 'rlm_staged.py', 'rlm_evidence.py'):
        (args.output / filename).write_text((repo / 'scripts/benchmarks' / filename).read_text(encoding='utf-8'), encoding='utf-8')
    save('manifest.json', {'model': model, 'credential_source': 'LLM Settings', 'reasoning_effort': 'max',
                          'context_path': str(args.context.resolve()),
                          'context_sha256': hashlib.sha256(args.context.read_bytes()).hexdigest(),
                          'source_hash': plan['source_hash'], 'expected_answers_visible_to_model': False,
                          'resume_from': str(args.resume_from.resolve()) if args.resume_from is not None else None,
                          'rerun_steps': args.rerun_step, 'repair_steps': args.repair_step})
    evidence = SourceEvidence(context, sql_inventory(context['files']))
    results, usage = {}, []
    todo = [step for step in bundles if not args.step or step in args.step]
    if not args.step:
        todo.append('synthesis')
    for step in todo:
        directory = args.output / step
        if step == 'synthesis':
            directory.mkdir()
            if any(r['status'] != 'completed' for r in results.values()):
                save('status.json', {'status': 'incomplete', 'reason': 'A node failed; synthesis withheld'})
                break
            bundle = prepare_synthesis(selection, bundles, results, plan['handler_step'])
            task = bundle['task']
        else:
            bundle = bundles[step]
            task = bundle['task']
        before, started = len(child.history), time.monotonic()
        previous_step = args.resume_from / step if args.resume_from is not None else None
        saved_response, format_feedback = None, None
        if previous_step is not None and step not in args.rerun_step and (previous_step / 'child-answer.txt').exists():
            previous_bundle = json.loads((previous_step / 'evidence.json').read_text(encoding='utf-8'))
            if evidence_identity(previous_bundle) != evidence_identity(bundle):
                raise ValueError(f'{step}: saved response has different evidence')
            # Revalidate using the exact input layout that the model actually saw.
            bundle = previous_bundle
            if step != 'synthesis':
                bundles[step] = bundle
            previous_calls = json.loads((previous_step / 'calls.json').read_text(encoding='utf-8'))
            raw_answer = (previous_step / 'child-answer.txt').read_text(encoding='utf-8')
            if (len(previous_calls) != 1 or previous_calls[0]['status'] != 'completed'
                or previous_calls[0]['answers'] != [raw_answer]):
                raise ValueError(f'{step}: saved answer does not match one completed model response')
            if step in args.repair_step:
                errors = json.loads((previous_step / 'validation.json').read_text(encoding='utf-8'))['errors']
                if not errors:
                    raise ValueError(f'{step}: no validation errors to repair')
                format_feedback = {'previous_response': (previous_step / 'child-answer.txt').read_text(encoding='utf-8'), 'errors': errors}
            else:
                saved_response = previous_step / 'child-answer.txt'
        try:
            result = run_staged(evidence, task, child, prompt, directory, prepared_evidence=bundle,
                                saved_response=saved_response, format_feedback=format_feedback, allow_evidence_gaps=True)
        except Exception as error:
            result = {'case': step, 'status': 'failed', 'error_type': type(error).__name__,
                      'answers': [], 'workflow_completed': False}
        result['seconds'] = time.monotonic() - started
        results[step] = result
        save(f'{step}/result.json', result)
        calls = public_calls(child, 'child')[before:]
        new_calls = len(calls)
        if saved_response is not None:
            result['reused_response'] = str(saved_response.resolve())
            save(f'{step}/result.json', result)
            calls = json.loads((previous_step / 'calls.json').read_text(encoding='utf-8'))
        save(f'{step}/calls.json', calls)
        usage.append({'step': step, 'calls': len(calls), 'input_tokens': sum(c['usage'].get('input_tokens', 0) for c in calls),
                      'cached_input_tokens': sum((c['usage'].get('input_tokens_details') or {}).get('cached_tokens', 0) or 0 for c in calls),
                      'output_tokens': sum(c['usage'].get('output_tokens', 0) for c in calls), 'seconds': result['seconds'],
                      'new_calls': new_calls, 'reused_from': str(saved_response.resolve()) if saved_response is not None else None})
        save('usage.json', usage)
        save('status.json', {'status': 'running', 'finished': len(results), 'total': len(todo)})
        print(f"{step}: {result['status']} ({len(results)}/{len(todo)})", flush=True)
        if result['status'] != 'completed':
            break  # Inspect the failed step before spending on downstream work.

    expected = json.loads(args.expected.read_text(encoding='utf-8'))
    answers = {a['id']: a for step, r in results.items() for a in r['answers']
               if step in (plan['handler_step'], 'synthesis')}
    facts = [{'id': q, 'expected': value, 'actual': answers.get(q, {}).get('value'),
              'correct': expected['snapshot_source_hash'] == plan['source_hash'] and answers.get(q, {}).get('value') == value}
             for q, value in expected['answers'].items()]
    save('evaluation.json', {'facts': facts, 'correct': sum(f['correct'] for f in facts), 'total': len(facts),
                             'answered': sum(f['actual'] is not None for f in facts),
                             'note': 'Known regression facts; open findings require separate review. Absent downstream answers are not incorrect model answers.'})
    complete = len(results) == len(todo) and all(r['status'] == 'completed' for r in results.values())
    save('status.json', {'status': 'completed' if complete else 'incomplete', 'completed_steps': sum(r['status']=='completed' for r in results.values()),
                         'total_steps': len(todo), 'synthesis': 'synthesis' in results,
                         'unresolved_evidence': {step: r['needs_evidence'] for step, r in results.items() if r.get('needs_evidence')}})
    lines = [f"# {selection['endpoint']}", '', 'Source analysis experiment. Model findings require review; unresolved dependencies remain explicit.', '']
    for step in (['synthesis'] if 'synthesis' in results else []) + [s for s in results if s != 'synthesis']:
        result = results[step]
        lines.extend([f'## {step}', '', f"Status: {result['status']}. [Evidence]({(args.output/step/'evidence.json').resolve()}) · [Result]({(args.output/step/'result.json').resolve()})", ''])
        if result.get('needs_evidence'):
            lines.extend(['Unresolved evidence requests:', '', *[f'- {gap}' for gap in result['needs_evidence']], ''])
        for answer in result['answers']:
            lines.extend([f"### {answer['id']} · {answer['value']}", '', answer['explanation'], ''])
            lines.extend(f"- `{c['path']}:{c['start_line']}-{c['end_line']}`" for c in answer['evidence'])
            lines.append('')
    (args.output / 'document.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f"Facts: {sum(f['correct'] for f in facts)}/{len(facts)}; status: {'completed' if complete else 'incomplete'}", flush=True)


if __name__ == '__main__':
    main()
