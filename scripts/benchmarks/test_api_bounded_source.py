import shutil
import unittest

from api_bounded_source import scoped_selections, synthesis_evidence


class BoundedSourceTests(unittest.TestCase):
    def test_scopes_cover_focused_questions_once(self):
        selection = {'handler_questions': [{'id': 'a'}],
                     'synthesis_questions': [{'id': 'b'}, {'id': 'api.flow'}]}
        scopes = [{'id': 'handler', 'scope': 'Handler only', 'question_ids': ['a'], 'findings': 'Local flow'},
                  {'id': 'state', 'scope': 'State only', 'question_ids': ['b'], 'findings': 'State flow'}]
        selected = scoped_selections(selection, scopes)
        self.assertEqual([q['id'] for q in selected[0]['handler_questions']], ['a', 'scope.handler'])
        self.assertEqual(selected[1]['scope'], 'State only')
        self.assertEqual(selected[0]['synthesis_questions'], [])
        with self.assertRaises(ValueError):
            scoped_selections(selection, scopes[:1])
        with self.assertRaises(ValueError):
            scoped_selections(selection, [scopes[0], {**scopes[1], 'question_ids': ['a', 'b']}])

    @unittest.skipUnless(shutil.which('ast-grep'), 'ast-grep required')
    def test_synthesis_preserves_full_handler_and_cited_function_without_other_same_named_method(self):
        files = {'p/api.go': 'package p\nfunc Handle() {\n q := New()\n q.Read()\n}\n',
                 'p/local/db.go': 'package local\nfunc Read() {\n if bad { return }\n query()\n}\n',
                 'p/other/db.go': 'package other\nfunc Read() { wrong() }\n'}
        question = {'id': 'state', 'allowed_values': ['described']}
        answer = {'id': 'state', 'value': 'described', 'explanation': 'Query depends on guard',
                  'evidence': [{'path': 'p/local/db.go', 'start_line': 4, 'end_line': 4, 'quote': 'query()'}]}
        selection = {'project_id': 'p', 'handler': {'name': 'Handle', 'path': 'api.go'},
                     'route_ranges': [], 'endpoint': '/test', 'handler_questions': [question],
                     'synthesis_questions': []}
        evidence, bundle = synthesis_evidence(files, selection,
            [{'id': 'state', 'scope': 'DB', 'questions': [question], 'answers': [answer]}])
        self.assertEqual(evidence.nodes, {})
        fragments = {f['path']: f for f in bundle['fragments']}
        self.assertEqual(set(fragments), {'p/api.go', 'p/local/db.go'})
        self.assertEqual([l['line'] for l in fragments['p/local/db.go']['lines']], [2, 3, 4, 5])
        self.assertEqual([l['line'] for l in fragments['p/api.go']['lines']], [2, 3, 4, 5])
        answer['evidence'][0]['quote'] = 'not in source'
        with self.assertRaisesRegex(ValueError, 'invalid step citations'):
            synthesis_evidence(files, selection,
                [{'id': 'state', 'scope': 'DB', 'questions': [question], 'answers': [answer]}])


if __name__ == '__main__':
    unittest.main()
