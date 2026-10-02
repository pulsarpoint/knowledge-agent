import shutil
import unittest

from rlm_evidence import SourceEvidence, check_answers, sql_inventory


class SqlInventoryTests(unittest.TestCase):
    def test_root_migrations_comments_quotes_and_schema_identity(self):
        files = {
            'p/db/migrations/1.up.sql': '-- CREATE TABLE fake(id int);\nCREATE TABLE "dgp"."items" (id int);',
            'p/module/schema.sql': 'CREATE TABLE items(id int); CREATE TABLE other.items(id int);',
            'p/db/migrations/1.down.sql': 'CREATE TABLE rollback_only(id int);',
            'other/schema.sql': 'CREATE TABLE items(id int);',
        }
        inventory = sql_inventory(files)
        identities = {(t['project_id'], t['schema'], t['name']) for t in inventory['tables'].values()}
        self.assertEqual(identities, {('p', 'dgp', 'items'), ('p', None, 'items'),
                                      ('p', 'other', 'items'), ('other', None, 'items')})
        self.assertEqual(len(inventory['excluded_down_migrations']), 1)

    def test_unicode_and_crlf_statement_locations_and_complete_cte(self):
        source = '-- café\r\nCREATE TABLE x(id int);\r\n-- name: Move :exec\r\nWITH d AS (DELETE FROM x RETURNING *)\r\nINSERT INTO y SELECT * FROM d;'
        inventory = sql_inventory({'p/queries.sql': source})
        query = next(s for s in inventory['statements'].values() if s['query_name'] == 'Move')
        self.assertEqual((query['start_line'], query['end_line']), (3, 5))
        self.assertIn('DELETE FROM x', query['code'])
        self.assertIn('INSERT INTO y', query['code'])
        self.assertEqual(query['code'], '\n'.join(source.splitlines()[2:5]).removesuffix(';'))

    def test_duplicate_declarations_are_retained(self):
        inventory = sql_inventory({'p/a.sql': 'CREATE TABLE x(id int);', 'p/b.sql': 'CREATE TABLE x(id int, extra text);'})
        table = next(iter(inventory['tables'].values()))
        self.assertEqual(len(inventory['tables']), 1)
        self.assertEqual(len(table['declarations']), 2)

    def test_invalid_sql_and_psql_commands_are_explicit_gaps(self):
        inventory = sql_inventory({'p/init.sql': 'CREATE TABLE x(id int);\n\\c db;\nCREATE TABLE y(id int);',
                                   'p/bad.sql': 'CREATE TABLE broken( ;'})
        self.assertEqual({g['path'] for g in inventory['gaps']}, {'p/init.sql', 'p/bad.sql'})

    def test_comments_or_string_literals_cannot_create_tables(self):
        inventory = sql_inventory({'p/a.sql': "-- CREATE TABLE fake(id int);\nSELECT 'CREATE TABLE fake2(id int)';"})
        self.assertEqual(inventory['tables'], {})


@unittest.skipUnless(shutil.which('ast-grep'), 'ast-grep CLI required')
class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.path = 'p/main.go'
        self.source = 'package main\nfunc Worker() {\n f := func() { println("x") }; f()\n}\n// Worker() is only a comment\nfunc main() { go Worker() }\nfunc Idle() {}\n'
        nodes = [dict(id=name, name=name, kind='function', language='go', file_key=self.path,
                      startLine=start, endLine=end) for name, start, end in [('Worker', 2, 4), ('main', 6, 6), ('Idle', 7, 7)]]
        self.context = {'files': {self.path: self.source}, 'graphs': {'p': {'nodes': nodes, 'edges': []}}}
        self.evidence = SourceEvidence(self.context, sql_inventory(self.context['files']))

    def test_complete_body_includes_nested_closure(self):
        result = self.evidence.get_function('p/Worker')
        self.assertEqual(result['code'], '\n'.join(self.source.splitlines()[1:4]))
        self.assertEqual(result['verification'], 'CodeGraph + ast-grep')
        self.assertEqual({c['expression'] for c in result['calls']}, {'println', 'f'})

    def test_call_scan_excludes_declarations_and_comments_even_when_graph_has_no_edge(self):
        result = self.evidence.get_callers('p/Worker')
        self.assertEqual(result['graph_edges'], [])
        self.assertEqual([c['line'] for c in result['syntax_candidates']], [6])
        self.assertEqual(self.evidence.get_callers('p/Idle')['syntax_candidates'], [])
        self.assertIn('unverified', result['note'])

    def test_graph_ast_boundary_mismatch_is_rejected(self):
        self.evidence.nodes['p/Worker']['endLine'] = 3
        with self.assertRaisesRegex(ValueError, 'boundaries differ'):
            self.evidence.get_function('p/Worker')

    def test_file_ranges_cannot_read_host_or_truncate_silently(self):
        with self.assertRaises(ValueError):
            self.evidence.get_source_range('/etc/passwd', 1, 2)
        with self.assertRaises(ValueError):
            self.evidence.get_source_range(self.path, 1, 999)


class AnswerChecksTests(unittest.TestCase):
    def setUp(self):
        self.questions = [{'id': 'q1', 'allowed_values': ['yes', 'no']}, {'id': 'q2', 'allowed_values': ['yes', 'no']}]
        self.files = {'p/a.go': 'func A() {}\nfunc B() {}'}
        self.citation = {'path': 'p/a.go', 'start_line': 1, 'end_line': 1, 'quote': 'func A() {}'}

    def test_missing_or_duplicate_question_is_not_silently_dropped(self):
        answer = {'id': 'q1', 'value': 'yes', 'evidence': [self.citation]}
        result = check_answers([answer, answer], self.questions, self.files)
        self.assertEqual(len(result['errors']), 2)

    def test_unknown_is_allowed_but_bad_quotes_and_vocabulary_are_rejected(self):
        answers = [{'id': 'q1', 'value': 'unknown', 'evidence': [self.citation]},
                   {'id': 'q2', 'value': 'maybe', 'evidence': [{**self.citation, 'quote': 'func B() {}'}]}]
        result = check_answers(answers, self.questions, self.files)
        self.assertEqual(len(result['errors']), 2)
        self.assertTrue(result['citations'][0]['matches'])
        self.assertFalse(result['citations'][1]['matches'])

    def test_matching_citation_does_not_assert_semantic_correctness(self):
        result = check_answers([{'id': q['id'], 'value': 'yes', 'evidence': [self.citation]} for q in self.questions],
                               self.questions, self.files)
        self.assertEqual(result['errors'], [])
        self.assertNotIn('correct', result)


if __name__ == '__main__':
    unittest.main()
