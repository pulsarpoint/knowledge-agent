import unittest

from api_source_rlm import prepare_source_task


class SourceOnlyBoundaryTests(unittest.TestCase):
    def test_model_inputs_exclude_graphs_previous_answers_and_other_projects(self):
        context = {'projects': [{'id': 'p', 'source_hash': 'hash'}],
                   'files': {'p/main.go': 'package main', 'q/secret.go': 'unselected'},
                   'graphs': {'p': {'nodes': ['wrong target'], 'edges': ['wrong edge']}},
                   'previous_answer': 'correction hint'}
        selection = {'project_id': 'p', 'snapshot_source_hash': 'hash', 'endpoint': '/test',
                     'handler': {'name': 'Handle', 'path': 'main.go'}, 'middleware': [],
                     'route_ranges': [], 'handler_questions': [{'id': 'handler'}],
                     'synthesis_questions': [{'id': 'flow'}], 'expected_answers': ['hidden']}
        inputs = prepare_source_task(context, selection)
        self.assertEqual(inputs['sources'], {'p/main.go': 'package main'})
        self.assertEqual(set(inputs), {'sources', 'task'})
        self.assertEqual(set(inputs['task']), {'endpoint', 'project_id', 'handler', 'middleware', 'route_ranges', 'questions'})
        self.assertEqual(inputs['task']['questions'], [{'id': 'handler'}, {'id': 'flow'}])
        with self.assertRaisesRegex(ValueError, 'another source snapshot'):
            prepare_source_task(context, {**selection, 'snapshot_source_hash': 'changed'})


if __name__ == '__main__':
    unittest.main()
