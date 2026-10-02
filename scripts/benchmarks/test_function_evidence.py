import shutil
import unittest

from function_evidence import collect_function_evidence, find_function, requested_resources
from rlm_evidence import SourceEvidence


@unittest.skipUnless(shutil.which('ast-grep'), 'ast-grep required')
class FunctionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.files = {
            'p/go.mod': 'module example.test/app\n',
            'p/api/api.go': 'package api\nimport local "example.test/app/state"\n'
                'type Input struct { Name string }\nconst unrelated = 123\n'
                'func Handle(input Input) {\n local.Change(input.Name)\n}\n'
                'func Other() { panic("unrelated body") }\n',
            'p/state/main.go': 'package state\nfunc Change(name string) {\n Read()\n}\n'
                'func Read() { query := New(pool); query.Get() }\n',
            'p/state/db.go': 'package state\ntype Queries struct { db int }\nvar pool int\n'
                'func New(db int) *Queries { return &Queries{db:db} }\n'
                'func (q *Queries) Get() { println("correct package") }\n'
                'type Other struct {}\nfunc (q *Other) Get() { println("other receiver") }\n',
            'p/scheduler/db.go': 'package scheduler\nfunc Read() { panic("wrong package") }\n'
                'type Queries struct {}\nfunc (q *Queries) Get() { panic("wrong SQL") }\n',
        }
        self.evidence = SourceEvidence({'files': self.files, 'graphs': {
            'p': {'nodes': [{'id': 'wrong-target', 'name': 'Read', 'file_key': 'p/scheduler/db.go'}]}}}, {})

    def test_extracts_type_and_import_but_not_other_function_or_constant(self):
        resources = [{'path': 'p/api/api.go', 'function': 'Handle'}]
        bundle = collect_function_evidence(self.evidence, {'id': 'handler'}, resources)
        text = '\n'.join(line['text'] for f in bundle['fragments'] for line in f['lines'])
        self.assertIn('type Input struct', text)
        self.assertIn('import local', text)
        self.assertIn('local.Change(input.Name)', text)
        self.assertNotIn('unrelated', text)
        added, gaps = requested_resources(bundle, ['D1'], resources)
        self.assertEqual(added, [{'path': 'p/state/main.go', 'function': 'Change'}])
        self.assertEqual(gaps, [])
        with self.assertRaisesRegex(ValueError, 'Unknown dependency'):
            requested_resources(bundle, ['../../etc/passwd'], resources)

    def test_same_package_dependency_and_receiver_identity(self):
        resources = [{'path': 'p/state/main.go', 'function': 'Change'}]
        bundle = collect_function_evidence(self.evidence, {'id': 'state'}, resources)
        added, _ = requested_resources(bundle, ['D1'], resources)
        self.assertEqual(added, [{'path': 'p/state/main.go', 'function': 'Read'}])
        expanded = collect_function_evidence(self.evidence, {'id': 'state'}, resources + added)
        self.assertFalse(any(f['path'].startswith('p/scheduler/') for f in expanded['fragments']))
        match = find_function(self.evidence, 'p/state/db.go', 'Get', 'Queries')
        self.assertIn('correct package', match['text'])
        self.assertNotIn('other receiver', match['text'])
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
            find_function(self.evidence, 'p/state/db.go', 'Get', None)
        receiver_call = next(d for d in expanded['observations']['dependencies'].values() if d['expression'] == 'query.Get')
        self.assertEqual(receiver_call['target'], {'path': 'p/state/db.go', 'function': 'Get', 'receiver': 'Queries'})
        self.assertEqual(receiver_call['supporting'], [{'path': 'p/state/db.go', 'function': 'New'}])

    def test_method_request_includes_constructor_type_and_full_sql_constant(self):
        self.files['p/state/db.go'] = self.files['p/state/db.go'].replace(
            'println("correct package")', 'println(activeQuery)') + '\nconst activeQuery = `SELECT id FROM records\nWHERE ends > NOW()\nORDER BY starts DESC LIMIT 1`\n'
        resource = {'path': 'p/state/main.go', 'function': 'Read'}
        bundle = collect_function_evidence(self.evidence, {}, [resource])
        request = next(key for key, d in bundle['observations']['dependencies'].items() if d['expression'] == 'query.Get')
        added, gaps = requested_resources(bundle, [request], [resource])
        self.assertEqual(added, [{'path': 'p/state/db.go', 'function': 'New'},
                                 {'path': 'p/state/db.go', 'function': 'Get', 'receiver': 'Queries'}])
        self.assertEqual(gaps, [])
        expanded = collect_function_evidence(self.evidence, {}, [resource, *added])
        text = '\n'.join(line['text'] for f in expanded['fragments'] for line in f['lines'])
        self.assertIn('return &Queries{db:db}', text)
        self.assertIn('type Queries struct { db int }', text)
        self.assertIn('WHERE ends > NOW()\nORDER BY starts DESC LIMIT 1', text)
        self.assertNotIn('wrong SQL', text)
        self.assertNotIn('other receiver', text)

    def test_imported_constructor_resolves_method_in_constructor_package(self):
        self.files['p/api/api.go'] += '\nfunc Read() { q := local.New(1); q.Get() }\n'
        bundle = collect_function_evidence(self.evidence, {}, [{'path': 'p/api/api.go', 'function': 'Read'}])
        call = next(d for d in bundle['observations']['dependencies'].values() if d['expression'] == 'q.Get')
        self.assertEqual(call['target'], {'path': 'p/state/db.go', 'function': 'Get', 'receiver': 'Queries'})
        self.assertEqual(call['supporting'][0]['path'], 'p/state/db.go')
        self.assertEqual(call['signature']['text'], 'func (q *Queries) Get()')
        self.assertFalse(call['signature']['body_included'])

    def test_local_callee_signature_preserves_multiple_results_without_body(self):
        self.files['p/state/main.go'] = (
            'package state\nimport "time"\n'
            'func Change(\n name string,\n) (*time.Location, *time.Location) {\n'
            ' panic("body must not be exposed")\n}\n')
        bundle = collect_function_evidence(self.evidence, {}, [{'path': 'p/api/api.go', 'function': 'Handle'}])
        signature = bundle['observations']['dependencies']['D1']['signature']
        self.assertEqual(signature, {'path': 'p/state/main.go', 'start_line': 3, 'end_line': 5,
                                    'text': 'func Change(\n name string,\n) (*time.Location, *time.Location)',
                                    'body_included': False})
        self.assertNotIn('body must not be exposed', str(bundle))
        self.assertNotIn('p/state/main.go', [f['path'] for f in bundle['fragments']])

    def test_signature_handles_utf8_crlf_inline_and_bodyless_declarations(self):
        for declaration, expected in [
            ('func Change(naïve string) (int, bool) { panic("hidden") }',
             'func Change(naïve string) (int, bool)'),
            ('func Change(\r\n naïve string,\r\n) (int, bool)',
             'func Change(\r\n naïve string,\r\n) (int, bool)'),
        ]:
            with self.subTest(declaration=declaration):
                files = {**self.files, 'p/state/main.go': 'package state\r\n' + declaration + '\r\n'}
                evidence = SourceEvidence({'files': files, 'graphs': {}}, {})
                bundle = collect_function_evidence(evidence, {}, [{'path': 'p/api/api.go', 'function': 'Handle'}])
                signature = bundle['observations']['dependencies']['D1']['signature']
                self.assertEqual(signature['text'], expected)
                self.assertEqual(signature['end_line'], 1 + len(expected.splitlines()))
                self.assertNotIn('hidden', str(bundle))

    def test_blank_gaps_added_but_omitted_code_stays_missing(self):
        self.files['p/api/spacing.go'] = (
            'package api\nconst usedA = 1\n\nconst hidden = 9\n\n'
            'const usedB = 2\n \t\nfunc Spacing() { println(usedA, usedB) }\n')
        bundle = collect_function_evidence(self.evidence, {}, [{'path': 'p/api/spacing.go', 'function': 'Spacing'}])
        fragment = next(f for f in bundle['fragments'] if f['path'].endswith('/spacing.go'))
        self.assertEqual([line['line'] for line in fragment['lines']], [1, 2, 6, 7, 8])
        self.assertNotIn('hidden', str(bundle))

    def test_reassignment_shadowing_and_nested_initializers_do_not_resolve(self):
        for source in [
            'func Read() { q := New(pool); q = New(pool); q.Get() }',
            'func Read() { q := New(pool); if ready { q := New(pool); q.Get() } }',
            'func Read() { if ready { q := New(pool); q.Get() } }',
            'func Read() { q := New(pool); run(func(q *Queries) { q.Get() }) }',
            'func Read() { var q = New(pool); q.Get() }',
            'func Read() { q, err := New(pool); q.Get() }',
        ]:
            with self.subTest(source=source):
                files = {**self.files, 'p/state/main.go': 'package state\n' + source}
                evidence = SourceEvidence({'files': files, 'graphs': {}}, {})
                bundle = collect_function_evidence(evidence, {}, [{'path': 'p/state/main.go', 'function': 'Read'}])
                call = next(d for d in bundle['observations']['dependencies'].values() if d['expression'] == 'q.Get')
                self.assertIsNone(call['target'])

    def test_interface_results_and_ambiguous_definitions_do_not_resolve(self):
        changes = [
            {'p/state/db.go': self.files['p/state/db.go'].replace('New(db int) *Queries', 'New(db int) Reader')
             + '\ntype Reader interface { Get() }\n'},
            {'p/state/alternative.go': 'package state\nfunc New(db int) *Queries { return nil }\n'},
            {'p/state/alternative.go': 'package state\nfunc (q *Queries) Get() {}\n'},
            {'p/state/alternative.go': 'package state\ntype Queries struct { other string }\n'},
            {'p/state/db.go': self.files['p/state/db.go'].replace(
                'func New(db int) *Queries { return &Queries{db:db} }', 'func New(db int) *Queries')},
            {'p/state/db.go': self.files['p/state/db.go'].replace(
                'func New(db int) *Queries { return &Queries{db:db} }',
                'func New[Queries any](db Queries) *Queries { return &db }')},
        ]
        for change in changes:
            with self.subTest(change=change):
                evidence = SourceEvidence({'files': {**self.files, **change}, 'graphs': {}}, {})
                bundle = collect_function_evidence(evidence, {}, [{'path': 'p/state/main.go', 'function': 'Read'}])
                call = next(d for d in bundle['observations']['dependencies'].values() if d['expression'] == 'query.Get')
                self.assertIsNone(call['target'])

    def test_ambiguous_build_variant_and_shadowed_alias_stay_unresolved(self):
        self.files['p/state/other.go'] = 'package state\nfunc Read() {}\n'
        bundle = collect_function_evidence(self.evidence, {}, [{'path': 'p/state/main.go', 'function': 'Change'}])
        self.assertIsNone(bundle['observations']['dependencies']['D1']['target'])
        self.assertIsNone(bundle['observations']['dependencies']['D1']['signature'])
        for shadow in ['func Handle(local Service) { local.Change("") }',
                       'func Handle() { local := getService(); local.Change("") }',
                       'func Handle() { var local Service; local.Change("") }',
                       'func Handle() { run(func(local Service) { local.Change("") }) }',
                       'func Handle() { var unused, local Service; local.Change("") }',
                       'func Handle() { for _, local := range services { local.Change("") } }']:
            files = {**self.files, 'p/api/api.go': 'package api\nimport local "example.test/app/state"\n' + shadow}
            evidence = SourceEvidence({'files': files, 'graphs': {}}, {})
            result = collect_function_evidence(evidence, {}, [{'path': 'p/api/api.go', 'function': 'Handle'}])
            call = next(d for d in result['observations']['dependencies'].values() if d['expression'] == 'local.Change')
            self.assertIsNone(call['target'])
            self.assertIsNone(call['signature'])

    def test_crlf_and_non_ascii_keep_original_line_numbers(self):
        self.files['p/api/lines.go'] = 'package api\r\n// č ć ž\r\nfunc Unicode() {\r\n println("č")\r\n}\r\n'
        bundle = collect_function_evidence(self.evidence, {}, [{'path': 'p/api/lines.go', 'function': 'Unicode'}])
        fragment = next(f for f in bundle['fragments'] if f['path'].endswith('/lines.go'))
        self.assertIn({'line': 4, 'text': ' println("č")'}, fragment['lines'])
        self.assertFalse(any('\r' in line['text'] for line in fragment['lines']))


if __name__ == '__main__':
    unittest.main()
