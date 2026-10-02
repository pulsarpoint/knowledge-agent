"""Offline checks for source/graph identity and inventory evidence."""

import hashlib
import unittest

from rlm_service import check_inventory, validate_context


class ServiceContextTests(unittest.TestCase):
    def setUp(self):
        self.context = {'schema_version': 1, 'service': {'name': 'Two projects'},
                        'projects': [], 'files': {}, 'file_manifest': {}, 'graphs': {}, 'omissions': []}
        for project, source in [('backend', 'func main() { run() }\r\n'), ('worker', 'func main() { consume() }\n')]:
            key = f'{project}/main.go'
            digest = hashlib.sha256(source.encode()).hexdigest()
            self.context['projects'].append({'id': project})
            self.context['files'][key] = source
            self.context['file_manifest'][key] = {'sha256': digest, 'bytes': len(source.encode())}
            self.context['graphs'][project] = {
                'nodes': [{'id': 'main', 'filePath': 'main.go', 'file_key': key}],
                'edges': [{'source': 'main', 'target': 'main'}],
                'files': [{'file_key': key, 'contentHash': digest}],
                'stats': {'nodeCount': 1, 'edgeCount': 1}, 'unresolved_references': [],
            }

    def test_same_paths_and_node_ids_in_different_projects_remain_distinct(self):
        summary = validate_context(self.context)
        self.assertEqual((summary['projects'], summary['files'], summary['nodes']), (2, 2, 2))
        self.assertNotEqual(self.context['files']['backend/main.go'], self.context['files']['worker/main.go'])

    def test_source_modified_after_export_is_rejected(self):
        self.context['files']['backend/main.go'] += '// new code'
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            validate_context(self.context)

    def test_old_index_with_new_source_is_rejected(self):
        self.context['graphs']['backend']['files'][0]['contentHash'] = 'old hash'
        with self.assertRaisesRegex(ValueError, 'Graph and source hashes differ'):
            validate_context(self.context)

    def test_missing_edge_target_is_rejected(self):
        self.context['graphs']['backend']['edges'][0]['target'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'endpoint is missing'):
            validate_context(self.context)

    def test_project_without_graph_is_rejected(self):
        del self.context['graphs']['worker']
        with self.assertRaisesRegex(ValueError, 'exactly one graph'):
            validate_context(self.context)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.files = {'project/main.go': 'func main() {\n    run()\n}'}
        self.inventory = {'overview': 'Example', 'apis': [], 'messaging': [], 'state': [],
                          'jobs': [{'name': 'startup', 'description': 'Calls run', 'origin': 'runtime',
                                    'evidence': [{'path': 'project/main.go', 'start_line': 2,
                                                  'end_line': 2, 'quote': 'run()'}]}],
                          'coverage': {'inspected_files': ['project/main.go'], 'uninspected_candidates': [], 'gaps': []}}

    def test_evidence_matches_but_does_not_claim_semantic_validation(self):
        result = check_inventory(self.inventory, self.files)
        self.assertEqual(result['structure_errors'], [])
        self.assertEqual(result['matching_citations'], 1)
        self.assertEqual(result['semantic_correctness'], 'not evaluated')

    def test_quote_at_wrong_line_is_detected(self):
        self.inventory['jobs'][0]['evidence'][0].update(start_line=1, end_line=1)
        self.assertEqual(check_inventory(self.inventory, self.files)['matching_citations'], 0)

    def test_missing_category_and_unknown_inspected_path_are_reported(self):
        del self.inventory['messaging']
        self.inventory['coverage']['inspected_files'] = ['other-project/main.go']
        self.assertEqual(len(check_inventory(self.inventory, self.files)['structure_errors']), 2)

    def test_malformed_evidence_is_reported_without_crashing(self):
        self.inventory['jobs'][0]['evidence'] = [None]
        self.assertEqual(len(check_inventory(self.inventory, self.files)['structure_errors']), 1)


if __name__ == '__main__':
    unittest.main()
