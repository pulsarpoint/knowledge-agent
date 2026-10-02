import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from function_evidence import collect_function_evidence
from rlm_evidence import SourceEvidence, sql_inventory
from rlm_staged import collect_evidence, run_staged


class StagedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.source = '-- name: ReadItems :many\nSELECT id FROM items;\n\n-- name: Hidden :exec\nDELETE FROM other;'
        self.context = {'files': {'p/queries.sql': self.source}, 'graphs': {}}
        self.evidence = SourceEvidence(self.context, sql_inventory(self.context['files']))
        self.task = {'id': 'test', 'resources': [{'query': 'ReadItems', 'path_suffix': 'queries.sql'}],
                     'questions': [{'id': 'q', 'allowed_values': ['yes', 'no']}]}
        self.answer = {'id': 'q', 'value': 'yes', 'explanation': 'Reads IDs.', 'observation_ids': [],
                       'evidence': [{'fragment_id': 'p/queries.sql:1-2', 'start_line': 2, 'end_line': 2}]}

    def child(self, reply, status='completed'):
        child = SimpleNamespace(history=[], requests=[])

        class Response:
            history = child.history

            def __call__(self, **kwargs):
                child.requests.append(kwargs)
                child.history.append({'response': SimpleNamespace(status=status)})
                return [{'text': reply, 'reasoning_content': 'PRIVATE_REASONING_FIXTURE'}]

        return Response(), child

    def test_complete_sql_and_one_child_call_before_validated_submission(self):
        child, calls = self.child(json.dumps({'answers': [self.answer]}))
        with TemporaryDirectory() as root:
            directory = Path(root)
            result = run_staged(self.evidence, self.task, child, 'Analyze the evidence.', directory)
            self.assertTrue(result['workflow_completed'])
            self.assertFalse(result['forced_extraction'])
            self.assertEqual(len(calls.requests), 1)
            bundle = json.loads(calls.requests[0]['messages'][1]['content'])
            self.assertEqual(bundle['fragments'][0]['lines'][-1], {'line': 2, 'text': 'SELECT id FROM items;'})
            self.assertNotIn('DELETE FROM other', json.dumps(bundle))
            submitted = json.loads((directory / 'submitted.json').read_text())
            self.assertEqual(submitted[0]['evidence'][0]['quote'], 'SELECT id FROM items;')
            self.assertEqual(submitted[0]['evidence'][0]['path'], 'p/queries.sql')
            self.assertNotIn('PRIVATE_REASONING_FIXTURE', ''.join(p.read_text() for p in directory.iterdir()))
            phases = json.loads((directory / 'phases.json').read_text())
            self.assertEqual([p['phase'] for p in phases], ['collect_evidence', 'child_analysis', 'validate', 'submit'])
            self.assertTrue(all(p['status'] == 'completed' for p in phases))

    def test_invalid_answers_never_submit_or_trigger_another_call(self):
        hidden_quote = {'fragment_id': 'p/queries.sql:1-2', 'start_line': 5, 'end_line': 5}
        for answers in ([], [self.answer, self.answer], [{**self.answer, 'value': 'maybe'}],
                        [{**self.answer, 'evidence': [{**self.answer['evidence'][0], 'fragment_id': 'invented'}]}],
                        [{**self.answer, 'evidence': [{**self.answer['evidence'][0], 'quote': 'wrong'}]}],
                        [{**self.answer, 'observation_ids': ['invented']}],
                        [{**self.answer, 'evidence': [hidden_quote]}], [{**self.answer, 'explanation': ''}]):
            with self.subTest(answers=answers), TemporaryDirectory() as root:
                directory = Path(root)
                child, calls = self.child(json.dumps({'answers': answers}))
                with self.assertRaisesRegex(ValueError, 'failed validation'):
                    run_staged(self.evidence, self.task, child, 'Analyze.', directory)
                self.assertEqual(len(calls.requests), 1)
                self.assertFalse((directory / 'submitted.json').exists())
                self.assertEqual(json.loads((directory / 'phases.json').read_text())[-1]['phase'], 'validate')
                self.assertTrue(json.loads((directory / 'validation.json').read_text())['errors'])

    def test_malformed_or_incomplete_responses_cannot_skip_analysis(self):
        for reply, status in [('```json\n{}\n```', 'completed'), ('[]', 'completed'),
                              ('{"answers":{}}', 'completed'),
                              (json.dumps({'answers': [self.answer]}), 'incomplete'),
                              (json.dumps({'answers': [self.answer], 'needs_evidence': ['caller source']}), 'completed')]:
            with self.subTest(reply=reply, status=status), TemporaryDirectory() as root:
                child, calls = self.child(reply, status)
                directory = Path(root)
                with self.assertRaises(ValueError):
                    run_staged(self.evidence, self.task, child, 'Analyze.', directory)
                self.assertEqual(len(calls.requests), 1)
                self.assertFalse((directory / 'submitted.json').exists())
                phases = json.loads((directory / 'phases.json').read_text())
                self.assertEqual(phases[-1]['phase'], 'child_analysis')
                self.assertEqual(phases[-1]['status'], 'failed')

    def test_missing_or_ambiguous_resources_fail_before_model_call(self):
        for files in ({}, {'p/queries.sql': self.source, 'other/queries.sql': self.source}):
            with self.subTest(files=files), TemporaryDirectory() as root:
                evidence = SourceEvidence({'files': files, 'graphs': {}}, sql_inventory(files))
                child, calls = self.child('{}')
                with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
                    run_staged(evidence, self.task, child, 'Analyze.', Path(root))
                self.assertEqual(calls.requests, [])

    @unittest.skipUnless(shutil.which('ast-grep'), 'ast-grep CLI required')
    def test_function_citations_cross_blank_lines_but_never_omitted_code(self):
        source = ('package main\n\nconst visible = 1\n\nfunc Worker() {\n println(visible)\n}\n'
                  '\nfunc Hidden() { panic("hidden") }\n\nfunc Later() { println(visible) }\n')
        evidence = SourceEvidence({'files': {'p/main.go': source}, 'graphs': {}}, {})
        task = {**self.task, 'mode': 'function', 'resources': [
            {'path': 'p/main.go', 'function': 'Worker'}, {'path': 'p/main.go', 'function': 'Later'}]}
        bundle = collect_function_evidence(evidence, task, task['resources'])
        for end_line, valid in [(7, True), (11, False)]:
            with self.subTest(end_line=end_line), TemporaryDirectory() as root:
                answer = {**self.answer, 'evidence': [{'fragment_id': 'S1', 'start_line': 1, 'end_line': end_line}]}
                child, _ = self.child(json.dumps({'answers': [answer]}))
                if valid:
                    result = run_staged(evidence, task, child, 'Analyze.', Path(root), prepared_evidence=bundle)
                    self.assertEqual(result['answers'][0]['evidence'][0]['quote'], '\n'.join(source.splitlines()[:7]))
                else:
                    with self.assertRaisesRegex(ValueError, 'failed validation'):
                        run_staged(evidence, task, child, 'Analyze.', Path(root), prepared_evidence=bundle)
                    self.assertFalse((Path(root) / 'submitted.json').exists())


@unittest.skipUnless(shutil.which('ast-grep'), 'ast-grep CLI required')
class WorkerEvidenceTests(unittest.TestCase):
    def setUp(self):
        files = {
            'p/worker.go': 'package main\nfunc Worker() {\n println("work")\n}\n\nfunc Idle() {}\n',
            'p/main.go': '//go:build !testing\n\npackage main\nfunc main() {\n if enabled { Worker() }\n}\n',
            'p/testing.go': '//go:build testing\n\npackage main\nfunc main() { Worker() }\n',
        }
        nodes = [dict(id=name, name=name, kind='function', language='go', file_key='p/worker.go',
                      startLine=start, endLine=end) for name, start, end in [('Worker', 2, 4), ('Idle', 6, 6)]]
        self.evidence = SourceEvidence({'files': files, 'graphs': {'p': {'nodes': nodes, 'edges': []}}}, {})

    def test_syntax_callers_supply_complete_branch_and_build_context_without_graph_edges(self):
        task = {'resources': [{'function': 'Worker', 'path_suffix': 'worker.go'}]}
        bundle = collect_evidence(self.evidence, task)
        self.assertEqual(bundle['gaps'], [])
        fragments = {(f['path'], f['start_line']): f for f in bundle['fragments']}
        self.assertEqual(fragments[('p/main.go', 1)]['lines'][0]['text'], '//go:build !testing')
        self.assertEqual(fragments[('p/testing.go', 1)]['lines'][0]['text'], '//go:build testing')
        self.assertEqual([l['text'] for l in fragments[('p/main.go', 4)]['lines']],
                         ['func main() {', ' if enabled { Worker() }', '}'])
        # The blank line at the end of the worker preamble stays represented.
        self.assertEqual(fragments[('p/main.go', 1)]['lines'][1], {'line': 2, 'text': ''})
        observation = bundle['observations']['callers:p/Worker']
        self.assertEqual(len(observation['scanned_files']), 3)
        self.assertEqual(len(observation['result']['syntax_candidates']), 2)

    def test_empty_call_scan_retains_scope_without_inventing_activation(self):
        bundle = collect_evidence(self.evidence, {'resources': [{'function': 'Idle', 'path_suffix': 'worker.go'}]})
        observation = bundle['observations']['callers:p/Idle']
        self.assertEqual(observation['result']['syntax_candidates'], [])
        self.assertEqual(observation['result']['graph_edges'], [])
        self.assertIn('do not prove', observation['scope'])
        self.assertNotIn('activation', observation['result'])


if __name__ == '__main__':
    unittest.main()
