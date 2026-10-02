"""Fixed evidence, child analysis, validation and submission for focused benchmarks."""

import json
import time
from pathlib import Path

import dspy

from rlm_evidence import SourceEvidence, check_answers


def collect_evidence(evidence: SourceEvidence, task: dict) -> dict:
    """Collect complete requested definitions and direct caller context, without model guesses."""
    fragments, observations, gaps = {}, {}, []

    def add_fragment(source: dict) -> None:
        path, start, end = source['path'], source['start_line'], source['end_line']
        key = f'{path}:{start}-{end}'
        # Read the original lines so a trailing blank line retains its number.
        lines = evidence.context['files'][path].splitlines()[start - 1:end]
        fragments[key] = {'id': key, 'path': path, 'start_line': start, 'end_line': end,
                          'verification': source.get('verification', 'frozen source range'),
                          'lines': [{'line': n, 'text': line} for n, line in enumerate(lines, start)]}

    for resource in task['resources']:
        if 'query' in resource:
            matches = evidence.get_sql_query(resource['query'], resource['path_suffix'])
            if len(matches) != 1:
                raise ValueError('Requested SQL statement is missing or ambiguous')
            add_fragment(matches[0])
            continue
        matches = evidence.search_symbols(resource['function'], resource['path_suffix'])
        if len(matches) != 1:
            raise ValueError('Requested function is missing or ambiguous')
        function_id = matches[0]['id']
        node = evidence.nodes[function_id]
        if node['language'] != 'go':
            raise ValueError('Staged function/caller collection currently requires Go')
        add_fragment(evidence.get_function(function_id))
        callers = evidence.get_callers(function_id)
        project_id = function_id.split('/')[0]
        observations[f'callers:{function_id}'] = {
            'tool': 'get_callers', 'function_id': function_id, 'result': callers,
            'scanned_files': sorted(p for p in evidence.context['files']
                                    if p.startswith(project_id + '/') and p.endswith('.go')),
            'scope': 'Saved graph and all included Go files, including build/test variants. '
                     'Name matches are candidates; no matches do not prove non-execution.',
        }
        paths = {node['file_key']}
        sites = {(c['path'], c['line']) for c in callers['syntax_candidates']}
        for edge in callers['graph_edges']:
            caller = evidence.nodes.get(f"{project_id}/{edge['source']}")
            if caller is None:
                gaps.append({'reason': 'Graph caller definition unavailable', 'edge': edge})
            else:
                add_fragment(evidence.get_function(f"{project_id}/{edge['source']}"))
                paths.add(caller['file_key'])
        for path, line in sorted(sites):
            paths.add(path)
            enclosing = [m for m in evidence.scan_go(path) if m['ruleId'] == 'definition'
                         and m['range']['start']['line'] + 1 <= line <= m['range']['end']['line'] + 1]
            if not enclosing:
                add_fragment(evidence.get_source_range(path, line, line))
                gaps.append({'path': path, 'line': line, 'reason': 'Call is outside a function; activation context unresolved'})
                continue
            for definition in enclosing:
                add_fragment({**evidence.get_source_range(path, definition['range']['start']['line'] + 1,
                                                         definition['range']['end']['line'] + 1),
                              'verification': 'ast-grep enclosing function'})
        for path in sorted(paths):
            definitions = [m for m in evidence.scan_go(path) if m['ruleId'] == 'definition']
            header_end = min(m['range']['start']['line'] for m in definitions)
            if header_end > 0:
                add_fragment(evidence.get_source_range(path, 1, header_end))
    return {'task': task, 'fragments': list(fragments.values()), 'observations': observations,
            'gaps': gaps, 'snapshot_omissions': evidence.context.get('omissions', [])}


def run_staged(evidence: SourceEvidence, task: dict, child: dspy.LM, prompt: str, directory: Path,
               *, prepared_evidence: dict | None = None, saved_response: Path | None = None,
               format_feedback: dict | None = None, allow_evidence_gaps: bool = False) -> dict:
    """One child call; failed collection, response or validation cannot become a submission."""
    phases = []

    def begin(name: str) -> None:
        if phases:
            phases[-1].update(status='completed', finished_at=time.time())
        phases.append({'phase': name, 'status': 'running', 'started_at': time.time()})
        (directory / 'phases.json').write_text(json.dumps(phases, indent=2), encoding='utf-8')
        print(f"{task['id']}: {name}", flush=True)

    try:
        begin('collect_evidence')
        bundle = collect_evidence(evidence, task) if prepared_evidence is None else prepared_evidence
        if bundle['task'] != task:
            raise ValueError('Prepared evidence belongs to a different task')
        (directory / 'evidence.json').write_text(json.dumps(bundle, indent=2), encoding='utf-8')

        begin('child_analysis')
        if saved_response is None:
            model_bundle = bundle
            if task.get('mode') in ('synthesis', 'function'):
                # The saved evidence retains the validator's numeric line records.
                # The model sees the same lines once, as ordinary numbered source.
                fragments = []
                for source in bundle['fragments']:
                    ranges = []
                    for line in source['lines']:
                        number = line['line']
                        if not ranges or number != ranges[-1][1] + 1:
                            ranges.append([number, number])
                        else:
                            ranges[-1][1] = number
                    fragments.append({'id': source['id'], 'path': source['path'], 'available_ranges': ranges,
                                      'numbered_source': '\n'.join(f"{line['line']}: {line['text']}"
                                                                  for line in source['lines'])})
                model_bundle = {**bundle, 'fragments': fragments}
            content = (json.dumps(model_bundle, separators=(',', ':')) if task.get('mode') in ('synthesis', 'function')
                       else json.dumps(bundle))
            messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': content}]
            if format_feedback is not None:
                messages.extend([{'role': 'assistant', 'content': format_feedback['previous_response']},
                                 {'role': 'user', 'content': 'Correct the response format using these validation errors: '
                                  + json.dumps(format_feedback['errors'])
                                  + '. Return the complete answer JSON with id, value, explanation, evidence and observation_ids. '
                                    'Each value must be one string from that question\'s allowed_values; do not copy the '
                                    'question/allowed_values input fields into the answer. For citations, every line '
                                    'must appear in that fragment\'s numbered source; some fragments have gaps. Split '
                                    'citations into supplied contiguous ranges instead of citing across gaps or '
                                    'beyond the chosen fragment. No expected factual answers '
                                    'are provided. Keep claims grounded in the same supplied evidence.'}])
                phases[-1]['format_feedback'] = format_feedback['errors']
            outputs = child(messages=messages)
            if getattr(child.history[-1]['response'], 'status', None) != 'completed':
                raise ValueError('Child response did not complete')
            if len(outputs) != 1 or not isinstance(outputs[0], dict) or not isinstance(outputs[0].get('text'), str):
                raise ValueError('Expected one JSON child response')
            response_text = outputs[0]['text']
        else:
            phases[-1]['reused_response'] = str(saved_response.resolve())
            response_text = saved_response.read_text(encoding='utf-8')
        # DSPy's Responses output can also contain reasoning; retain only text.
        (directory / 'child-answer.txt').write_text(response_text, encoding='utf-8')
        payload = json.loads(response_text)
        if not isinstance(payload, dict) or not isinstance(payload.get('answers'), list):
            raise ValueError('Child response must contain an answers list')
        gaps = payload.get('needs_evidence', [])
        if not isinstance(gaps, list) or any(not isinstance(g, str) for g in gaps):
            raise ValueError('Evidence requests must be a list of strings')
        if gaps and not allow_evidence_gaps:
            raise ValueError('Child requested more evidence; submission withheld')

        begin('validate')
        answers = payload['answers']
        fragments = {f['id']: f for f in bundle['fragments']}
        selection_errors, normalized_citations = [], []
        for answer in answers:
            if not isinstance(answer, dict) or not isinstance(answer.get('evidence'), list):
                continue  # check_answers reports malformed answers.
            quotes = []
            for citation in answer['evidence']:
                if not isinstance(citation, dict) or set(citation) != {'fragment_id', 'start_line', 'end_line'}:
                    selection_errors.append(f"{answer.get('id')}: expected a fragment ID and line range")
                    continue
                fragment_id = citation['fragment_id']
                fragment = fragments.get(fragment_id) if isinstance(fragment_id, str) else None
                start, end = citation['start_line'], citation['end_line']
                if fragment is None and type(start) is int and type(end) is int:
                    # Some responses label a selected subrange instead of its enclosing
                    # fragment. Accept only that exact source locator, never fuzzy IDs.
                    fragment = next((f for f in fragments.values()
                                     if fragment_id == f"{f['path']}:{start}-{end}"
                                     and f['start_line'] <= start <= end <= f['end_line']
                                     and set(range(start, end + 1)).issubset({line['line'] for line in f['lines']})), None)
                    if fragment is not None:
                        normalized_citations.append({'question_id': answer.get('id'), 'from': fragment_id, 'to': fragment['id']})
                        citation = {**citation, 'fragment_id': fragment['id']}
                if fragment is None or type(start) is not int or type(end) is not int or not (
                    fragment['start_line'] <= start <= end <= fragment['end_line']
                ) or not set(range(start, end + 1)).issubset({line['line'] for line in fragment['lines']}):
                    selection_errors.append(f"{answer.get('id')}: citation outside supplied evidence: {fragment_id}, lines {start}-{end}")
                    continue
                quotes.append({**citation, 'path': fragment['path'],
                               'quote': evidence.get_source_range(fragment['path'], start, end)['code']})
            answer['evidence'] = quotes
        checks = check_answers(answers, task['questions'], evidence.context['files'])
        checks['normalized_citations'] = normalized_citations
        checks['errors'].extend(selection_errors)
        allowed_refs = set(bundle['observations'])
        aliases = {}
        pending = list(bundle['observations'].items())
        while pending:
            prefix, value = pending.pop()
            if isinstance(value, dict):
                for key, nested in value.items():
                    reference = f'{prefix}.{key}'
                    allowed_refs.add(reference)
                    aliases.setdefault(key, set()).add(reference)
                    pending.append((reference, nested))
        # Accept an exact nested key only when it identifies one supplied value.
        # Duplicate keys still require a fully qualified reference.
        allowed_refs.update(key for key, paths in aliases.items() if len(paths) == 1)
        for answer in answers:
            if not isinstance(answer, dict):
                continue  # Already reported by check_answers.
            if not isinstance(answer.get('explanation'), str) or not answer['explanation'].strip():
                checks['errors'].append(f"{answer.get('id')}: missing explanation")
            refs = answer.get('observation_ids', [])
            if not isinstance(refs, list) or any(not isinstance(r, str) or r not in allowed_refs for r in refs):
                checks['errors'].append(f"{answer.get('id')}: unknown observation reference")
        (directory / 'validation.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
        if checks['errors']:
            raise ValueError('Child answer failed validation; submission withheld')

        begin('submit')
        (directory / 'submitted.json').write_text(json.dumps(answers, indent=2), encoding='utf-8')
        phases[-1].update(status='completed', finished_at=time.time())
        return {'case': task['id'], 'status': 'completed', 'answers': answers, 'checks': checks,
                'forced_extraction': False, 'completion_path': 'staged', 'workflow_completed': True,
                'needs_evidence': gaps, 'has_evidence_requests': bool(gaps)}
    finally:
        if phases and phases[-1]['status'] == 'running':
            phases[-1].update(status='failed', finished_at=time.time())
        (directory / 'phases.json').write_text(json.dumps(phases, indent=2), encoding='utf-8')
