import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from api_staged import evidence_identity, prepare_endpoint, prepare_synthesis
from rlm_staged import collect_evidence, run_staged
import test_rlm_staged


@unittest.skipUnless(shutil.which('ast-grep'), 'ast-grep CLI required')
class EndpointEvidenceTests(unittest.TestCase):
    def setUp(self):
        files = {'p/main.go': 'package main\nfunc Handle(flag bool) {\n if flag {\n  Update(1)\n }\n}\n'
                              'func Update(n int) { Store(n) }\nfunc Unrelated() {}\n',
                 'p/queries.sql.go': 'package main\nconst statement = `UPDATE items SET count=$1`\n'
                                      'func Store(n int) { exec(statement, n) }\n'}

        def node(name, file, start, end, kind='function'):
            return {'id': name, 'name': name, 'qualifiedName': name, 'filePath': file,
                    'file_key': f'p/{file}', 'startLine': start, 'endLine': end,
                    'kind': kind, 'language': 'go', 'source_available': True}

        nodes = [node('Handle', 'main.go', 2, 6), node('Update', 'main.go', 7, 7),
                 node('Unrelated', 'main.go', 8, 8), node('Store', 'queries.sql.go', 3, 3),
                 node('statement', 'queries.sql.go', 2, 2, 'constant')]
        edges = [{'source': 'Handle', 'target': 'Update', 'kind': 'calls', 'line': 4,
                  'metadata': {'refName': 'Update', 'confidence': .4}},
                 {'source': 'Update', 'target': 'Store', 'kind': 'calls', 'line': 7,
                  'metadata': {'refName': 'Store'}},
                 {'source': 'Store', 'target': 'statement', 'kind': 'references'},
                 {'source': 'Handle', 'target': 'Unrelated', 'kind': 'references'}]
        self.context = {'files': files, 'graphs': {'p': {'source_hash': 'frozen', 'nodes': nodes,
                                                       'edges': edges, 'unresolved_references': []}}}
        self.selection = {'project_id': 'p', 'snapshot_source_hash': 'frozen', 'endpoint': 'POST /test',
                          'handler': {'name': 'Handle', 'path': 'main.go'}, 'middleware': [],
                          'route_ranges': [{'path': 'main.go', 'start_line': 1, 'end_line': 1}],
                          'handler_questions': []}

    def test_follows_calls_once_and_embeds_generated_sql_without_running_it(self):
        plan, bundles = prepare_endpoint(self.context, self.selection)
        self.assertEqual(plan['steps'], ['01-Handle', '02-Update'])
        self.assertEqual(plan['reachable_nodes'], 3)
        text = json.dumps(bundles['02-Update']['fragments'])
        self.assertIn('UPDATE items SET count=$1', text)
        self.assertIn('Store(n int)', text)
        self.assertNotIn('func Unrelated()', text)
        refs = bundles['01-Handle']['observations']['call_context']['noncall_function_references']
        self.assertEqual(refs[0]['target'], 'Unrelated')

    def test_preserves_call_argument_condition_and_candidate_confidence(self):
        _, bundles = prepare_endpoint(self.context, self.selection)
        context = bundles['02-Update']['observations']['call_context']['incoming'][0]
        self.assertEqual(context['resolution']['confidence'], .4)
        guard = bundles['02-Update']['observations']['call_context']['guards'][context['guards'][0]]
        self.assertEqual((guard['condition'], guard['position']), ('flag', 'consequence'))
        fragment = next(f for f in bundles['02-Update']['fragments'] if f['id'] == context['source_fragment'])
        self.assertIn('Update(1)', json.dumps(fragment))

    def test_refuses_changed_snapshot_or_ambiguous_seed(self):
        with self.assertRaisesRegex(ValueError, 'another source snapshot'):
            prepare_endpoint(self.context, {**self.selection, 'snapshot_source_hash': 'changed'})
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
            prepare_endpoint(self.context, {**self.selection, 'handler': {'name': 'Missing', 'path': 'main.go'}})


class PreparedEvidenceTests(unittest.TestCase):
    def test_reuse_identity_ignores_only_guard_order(self):
        a = {'observations': {'call_context': {'incoming': [{'guards': ['outer', 'inner'], 'line': 4}]}}}
        b = {'observations': {'call_context': {'incoming': [{'guards': ['inner', 'outer'], 'line': 4}]}}}
        self.assertEqual(evidence_identity(a), evidence_identity(b))
        self.assertEqual(a['observations']['call_context']['incoming'][0]['guards'], ['outer', 'inner'])
        b['observations']['call_context']['incoming'][0]['line'] = 5
        self.assertNotEqual(evidence_identity(a), evidence_identity(b))

    def test_documentation_keeps_evidence_requests_explicit(self):
        fixture = test_rlm_staged.StagedWorkflowTests()
        fixture.setUp()
        gaps = ['External driver implementation is unavailable.']
        child, calls = fixture.child(json.dumps({'answers': [fixture.answer], 'needs_evidence': gaps}))
        with TemporaryDirectory() as root:
            result = run_staged(fixture.evidence, fixture.task, child, 'Analyze.', Path(root), allow_evidence_gaps=True)
            self.assertEqual(result['needs_evidence'], gaps)
            self.assertTrue(result['has_evidence_requests'])
            self.assertEqual(len(calls.requests), 1)

    def test_format_feedback_uses_validator_errors_without_expected_answers(self):
        fixture = test_rlm_staged.StagedWorkflowTests()
        fixture.setUp()
        child, calls = fixture.child(json.dumps({'answers': [fixture.answer]}))
        with TemporaryDirectory() as root:
            result = run_staged(fixture.evidence, fixture.task, child, 'Analyze.', Path(root),
                                format_feedback={'previous_response': '{"answers":[]}', 'errors': ['q: missing value']})
            self.assertTrue(result['workflow_completed'])
            messages = calls.requests[0]['messages']
            self.assertEqual(messages[-2], {'role': 'assistant', 'content': '{"answers":[]}'})
            self.assertIn('q: missing value', messages[-1]['content'])
            self.assertEqual(len(calls.requests), 1)

    def test_synthesis_supplies_cited_lines_once_without_mutating_results(self):
        fragment = {'id': 'p/helper.go:1-3', 'path': 'p/helper.go', 'start_line': 1, 'end_line': 3,
                    'lines': [{'line': n, 'text': str(n)} for n in range(1, 4)]}
        bundles = {'handler': {'task': {'function': {}}, 'fragments': []},
                   'helper': {'task': {'function': {}}, 'fragments': [fragment]}}
        citations = [{'fragment_id': fragment['id'], 'start_line': n, 'end_line': n,
                      'path': fragment['path'], 'quote': f'quote{n}'} for n in (1, 3)]
        results = {'handler': {'answers': []}, 'helper': {'answers': [{'id': 'state', 'evidence': citations}]}}
        bundle = prepare_synthesis({'endpoint': 'POST /', 'synthesis_questions': []}, bundles, results, 'handler')
        self.assertEqual([line['line'] for line in bundle['fragments'][0]['lines']], [1, 3])
        self.assertNotIn('quote', bundle['observations']['helper']['answers'][0]['evidence'][0])
        self.assertEqual(results['helper']['answers'][0]['evidence'][0]['quote'], 'quote1')
        self.assertEqual(len(fragment['lines']), 3)

    def test_synthesis_merges_overlap_preserves_findings_and_excludes_uncited_callers(self):
        def fragment(path, start, texts, kind):
            return {'id': f'{path}:{start}-{start + len(texts) - 1}', 'path': path,
                    'start_line': start, 'end_line': start + len(texts) - 1, 'kind': kind,
                    'lines': [{'line': start + i, 'text': text} for i, text in enumerate(texts)]}

        handler = fragment('p/main.go', 1, ['func Handle() {', '  Read()', '  if invalid { return }', '}'],
                           'AST-verified function')
        route = fragment('p/routes.go', 8, ['register(Handle)'], 'explicit route selection')
        unrelated = fragment('p/test.go', 1, ['Handle()'], 'incoming call arguments')
        overlap = fragment('p/main.go', 2, ['  Read()', '  if invalid { return }'], 'incoming call arguments')
        body = fragment('p/read.go', 20, ['func Read() {', '  Mutate()', '}'], 'AST-verified function')
        answers = [{'id': 'state', 'value': 'described', 'explanation': 'Read delegates the effect to Mutate.',
                    'observation_ids': ['original-node-only'], 'evidence': [
                        {'fragment_id': source['id'], 'start_line': source['start_line'],
                         'end_line': source['end_line'], 'path': source['path'], 'quote': 'original quote'}
                        for source in (overlap, body, overlap)]}]
        bundles = {'handler': {'task': {'function': {'file_key': 'p/main.go'}},
                               'fragments': [handler, route, unrelated]},
                   'reader': {'task': {'function': {'qualifiedName': 'Read', 'file_key': 'p/read.go'}},
                              'fragments': [overlap, body]}}
        results = {'handler': {'answers': []},
                   'reader': {'answers': answers, 'needs_evidence': ['Mutate implementation missing.']}}
        original = json.dumps({'bundles': bundles, 'results': results})
        bundle = prepare_synthesis({'endpoint': 'POST /', 'synthesis_questions': []}, bundles, results, 'handler')
        sources = {f['path']: f for f in bundle['fragments']}
        self.assertEqual(set(sources), {'p/main.go', 'p/routes.go', 'p/read.go'})
        self.assertEqual(sources['p/main.go']['lines'], handler['lines'])
        self.assertEqual(sources['p/routes.go']['lines'], route['lines'])
        observation = bundle['observations']['reader']
        self.assertEqual(observation['source_id'], sources['p/read.go']['id'])
        self.assertEqual(observation['answers'][0]['explanation'], answers[0]['explanation'])
        self.assertEqual(observation['needs_evidence'], ['Mutate implementation missing.'])
        self.assertEqual(len(observation['answers'][0]['evidence']), 2)
        self.assertEqual(observation['answers'][0]['evidence'][0]['fragment_id'], sources['p/main.go']['id'])
        self.assertNotIn('observation_ids', observation['answers'][0])
        self.assertEqual(json.dumps({'bundles': bundles, 'results': results}), original)

    def test_synthesis_rejects_conflicting_source_and_missing_cited_lines(self):
        fragment = {'id': 'handler', 'path': 'p/main.go', 'start_line': 1, 'end_line': 2,
                    'lines': [{'line': 1, 'text': 'first'}, {'line': 2, 'text': 'second'}]}
        bundles = {'handler': {'task': {'function': {}}, 'fragments': [fragment]},
                   'helper': {'task': {'function': {}}, 'fragments': [
                       {**fragment, 'id': 'helper', 'lines': [{'line': 1, 'text': 'changed'}]}]}}
        results = {'handler': {'answers': []}, 'helper': {'answers': [
            {'id': 'state', 'evidence': [{'fragment_id': 'helper', 'start_line': 1, 'end_line': 1}]}]}}
        selection = {'endpoint': 'POST /', 'synthesis_questions': []}
        with self.assertRaisesRegex(ValueError, 'Conflicting source'):
            prepare_synthesis(selection, bundles, results, 'handler')
        results['helper']['answers'][0]['evidence'][0]['end_line'] = 2
        with self.assertRaisesRegex(ValueError, 'unavailable source'):
            prepare_synthesis(selection, bundles, results, 'handler')

    def test_synthesis_sends_numbered_source_but_validates_against_original_lines(self):
        fixture = test_rlm_staged.StagedWorkflowTests()
        fixture.setUp()
        task = {**fixture.task, 'mode': 'synthesis'}
        bundle = collect_evidence(fixture.evidence, task)
        child, calls = fixture.child(json.dumps({'answers': [fixture.answer]}))
        with TemporaryDirectory() as root:
            result = run_staged(fixture.evidence, task, child, 'Analyze.', Path(root), prepared_evidence=bundle)
            model_input = json.loads(calls.requests[0]['messages'][1]['content'])
            source = model_input['fragments'][0]
            self.assertNotIn('lines', source)
            self.assertEqual(source['numbered_source'], '\n'.join(
                f"{line['line']}: {line['text']}" for line in bundle['fragments'][0]['lines']))
            saved = json.loads((Path(root) / 'evidence.json').read_text())
            self.assertEqual(saved, bundle)
            self.assertTrue(result['workflow_completed'])
            self.assertTrue(result['answers'][0]['evidence'][0]['quote'])

    def test_synthesis_marks_gaps_and_still_rejects_a_citation_crossing_them(self):
        fixture = test_rlm_staged.StagedWorkflowTests()
        fixture.setUp()
        task = {**fixture.task, 'mode': 'synthesis'}
        bundle = collect_evidence(fixture.evidence, task)
        fragment = bundle['fragments'][0]
        fragment['lines'] = [line for line in fragment['lines'] if line['line'] != 2]
        child, calls = fixture.child(json.dumps({'answers': [fixture.answer]}))
        with TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'failed validation'):
                run_staged(fixture.evidence, task, child, 'Analyze.', Path(root), prepared_evidence=bundle)
            source = json.loads(calls.requests[0]['messages'][1]['content'])['fragments'][0]
            shown = {n for start, end in source['available_ranges'] for n in range(start, end + 1)}
            self.assertEqual(shown, {line['line'] for line in fragment['lines']})
            self.assertNotIn(2, shown)
            self.assertFalse((Path(root) / 'submitted.json').exists())

    def test_subrange_locator_is_normalized_only_for_supplied_source_lines(self):
        fixture = test_rlm_staged.StagedWorkflowTests()
        fixture.setUp()
        for locator, start, end, valid in [('p/queries.sql:2-2', 2, 2, True),
                                           ('invented:2-2', 2, 2, False),
                                           ('p/queries.sql:5-5', 5, 5, False)]:
            with self.subTest(locator=locator), TemporaryDirectory() as root:
                answer = {**fixture.answer, 'evidence': [{'fragment_id': locator, 'start_line': start, 'end_line': end}]}
                child, _ = fixture.child(json.dumps({'answers': [answer]}))
                if valid:
                    result = run_staged(fixture.evidence, fixture.task, child, 'Analyze.', Path(root))
                    self.assertEqual(result['answers'][0]['evidence'][0]['fragment_id'], 'p/queries.sql:1-2')
                    self.assertEqual(result['checks']['normalized_citations'][0]['from'], locator)
                else:
                    with self.assertRaisesRegex(ValueError, 'failed validation'):
                        run_staged(fixture.evidence, fixture.task, child, 'Analyze.', Path(root))

    def test_saved_response_is_revalidated_without_a_call_and_nested_references_must_exist(self):
        fixture = test_rlm_staged.StagedWorkflowTests()
        fixture.setUp()
        bundle = collect_evidence(fixture.evidence, fixture.task)
        bundle['observations'] = {'call_context': {'outgoing': [], 'nested': {'value': False}, 'other': {'value': True},
                                                 'guards': {'p/main.go:5:if:consequence': {'condition': 'flag'}}}}
        for reference in ('call_context.outgoing', 'outgoing', 'call_context.nested.value',
                          'p/main.go:5:if:consequence', 'call_context.missing', 'value'):
            with self.subTest(reference=reference), TemporaryDirectory() as root:
                directory = Path(root) / 'result'
                directory.mkdir()
                response = Path(root) / 'saved.txt'
                response.write_text(json.dumps({'answers': [{**fixture.answer, 'observation_ids': [reference]}]}))
                child, calls = fixture.child('{}')
                if reference.endswith('missing') or reference == 'value':
                    with self.assertRaisesRegex(ValueError, 'failed validation'):
                        run_staged(fixture.evidence, fixture.task, child, 'Analyze.', directory,
                                   prepared_evidence=bundle, saved_response=response)
                    self.assertFalse((directory / 'submitted.json').exists())
                else:
                    result = run_staged(fixture.evidence, fixture.task, child, 'Analyze.', directory,
                                        prepared_evidence=bundle, saved_response=response)
                    self.assertTrue(result['workflow_completed'])
                self.assertEqual(calls.requests, [])

    def test_citation_cannot_select_unshown_lines_inside_a_fragment(self):
        fixture = test_rlm_staged.StagedWorkflowTests()
        fixture.setUp()
        bundle = collect_evidence(fixture.evidence, fixture.task)
        bundle['fragments'][0]['lines'] = bundle['fragments'][0]['lines'][:1]
        child, calls = fixture.child(json.dumps({'answers': [fixture.answer]}))
        with TemporaryDirectory() as root:
            directory = Path(root)
            with self.assertRaisesRegex(ValueError, 'failed validation'):
                run_staged(fixture.evidence, fixture.task, child, 'Analyze.', directory, prepared_evidence=bundle)
            self.assertFalse((directory / 'submitted.json').exists())
            self.assertEqual(len(calls.requests), 1)

    def test_prepared_evidence_cannot_swap_tasks(self):
        fixture = test_rlm_staged.StagedWorkflowTests()
        fixture.setUp()
        bundle = collect_evidence(fixture.evidence, fixture.task)
        bundle['task'] = {**fixture.task, 'id': 'different'}
        child, calls = fixture.child('{}')
        with TemporaryDirectory() as root, self.assertRaisesRegex(ValueError, 'different task'):
            run_staged(fixture.evidence, fixture.task, child, 'Analyze.', Path(root), prepared_evidence=bundle)
        self.assertEqual(calls.requests, [])


if __name__ == '__main__':
    unittest.main()
