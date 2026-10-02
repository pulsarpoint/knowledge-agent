"""Deterministic SQL inventory and source tools for frozen RLM experiments."""

import hashlib
import json
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from pglast.parser import ParseError, parse_sql_json, split


def sql_nodes(value: object, kind: str) -> list[dict]:
    """Find one PostgreSQL AST node kind, including statements inside CTEs."""
    if isinstance(value, dict):
        return ([value[kind]] if kind in value else []) + [
            node for child in value.values() for node in sql_nodes(child, kind)]
    if isinstance(value, list):
        return [node for child in value for node in sql_nodes(child, kind)]
    return []


def sql_inventory(files: dict[str, str]) -> dict:
    """Keep schema-qualified declarations distinct; do not infer search_path or deployment."""
    tables, statements, gaps, excluded = {}, {}, [], []
    for path, source in sorted(files.items()):
        if not path.endswith('.sql'):
            continue
        if path.endswith('.down.sql'):
            excluded.append(path)
            continue
        try:
            parse_sql_json(source)
        except ParseError:
            gaps.append({'path': path, 'reason': 'File is not wholly PostgreSQL SQL; statement-level parsing follows. '
                         'Scanner may omit psql commands; this file needs review.'})
        try:
            ranges = split(source, with_parser=False, only_slices=True)
        except ParseError:
            gaps.append({'path': path, 'reason': 'SQL statement splitting failed'})
            continue
        for bounds in ranges:
            code = source[bounds]
            start = source.count('\n', 0, bounds.start) + 1
            end = start + code.count('\n')
            header = re.match(r'(?:\s*--[^\n]*(?:\n|$))*', code).group()
            names = re.findall(r'^\s*--\s+name:\s+(\w+)\s+:\w+', header, re.M)
            try:
                tree = json.loads(parse_sql_json(code))
            except ParseError:
                gaps.append({'path': path, 'start_line': start, 'end_line': end,
                             'reason': 'Not parsed as PostgreSQL SQL; may contain psql commands or another dialect'})
                continue
            if not tree['stmts']:
                continue
            statement_id = f'{path}:{start}:{bounds.start}'
            relations = sql_nodes(tree, 'RangeVar')
            statements[statement_id] = {
                'id': statement_id, 'path': path, 'start_line': start, 'end_line': end,
                'code': '\n'.join(code.splitlines()),
                'query_name': names[-1] if names else None,
                'kinds': [next(iter(s['stmt'])) for s in tree['stmts']],
                'relations': [{'schema': r.get('schemaname'), 'name': r['relname']} for r in relations],
            }
            for node in sql_nodes(tree, 'CreateStmt'):
                relation = node['relation']
                identity = [path.split('/')[0], relation.get('schemaname'), relation['relname']]
                table_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
                table = tables.setdefault(table_id, {'id': table_id, 'project_id': identity[0],
                    'schema': identity[1], 'name': identity[2], 'status': 'declared', 'declarations': []})
                table['declarations'].append(statement_id)
    return {'tables': tables, 'statements': statements, 'gaps': gaps, 'excluded_down_migrations': excluded,
            'note': 'Declaration inventory, not a replayed or deployed schema. Unqualified names remain schema-unknown. '
                    'ALTER/DROP and historical declarations require separate schema evolution analysis.'}


class SourceEvidence:
    """Read a frozen snapshot; combine saved CodeGraph relationships with fresh ast-grep syntax."""

    def __init__(self, context: dict, inventory: dict):
        self.context = context
        self.inventory = inventory
        self.nodes = {f'{pid}/{n["id"]}': n for pid, graph in context['graphs'].items() for n in graph['nodes']}
        self.syntax: dict[str, list[dict]] = {}

    def get_source_range(self, path: str, start_line: int, end_line: int) -> dict:
        """Read inclusive 1-based lines. Returns {path,start_line,end_line,code}, not a string.

        Use result['code'].splitlines() to read its lines, numbered from result['start_line'].
        Never reads host files. Out-of-range requests fail and report the available line count.
        """
        if path not in self.context['files']:
            raise ValueError('Path is not in the frozen source snapshot')
        lines = self.context['files'][path].splitlines()
        if not 1 <= start_line <= end_line <= len(lines):
            raise ValueError(f'Invalid source line range; this file has lines 1 through {len(lines)}')
        return {'path': path, 'start_line': start_line, 'end_line': end_line,
                'code': '\n'.join(lines[start_line - 1:end_line])}

    def search_symbols(self, name: str, path_suffix: str = '') -> list[dict]:
        """Find all exact-name CodeGraph functions/methods. Select an ID; do not guess overloads."""
        return [{'id': key, **{field: n[field] for field in ('name', 'kind', 'file_key', 'startLine', 'endLine')}}
                for key, n in self.nodes.items() if n['name'] == name
                and n['kind'] in ('function', 'method') and n['file_key'].endswith(path_suffix)]

    def scan_go(self, path: str) -> list[dict]:
        if path in self.syntax:
            return self.syntax[path]
        if path not in self.context['files'] or not path.endswith('.go'):
            raise ValueError('Go syntax inspection requires a saved .go file')
        # Same ast-grep declaration/call rules used by the application's code_search tool.
        rules = [
            {'id': 'definition', 'language': 'Go', 'rule': {'all': [
                {'any': [{'kind': 'function_declaration'}, {'kind': 'method_declaration',
                    'has': {'field': 'receiver', 'pattern': '$RECEIVER'}}]},
                {'has': {'field': 'name', 'pattern': '$NAME'}},
                {'any': [{'has': {'field': 'body', 'pattern': '$BODY'}},
                         {'not': {'has': {'field': 'body', 'kind': 'block'}}}]}]}},
            {'id': 'function-result', 'language': 'Go', 'rule': {'all': [
                {'kind': 'function_declaration'}, {'has': {'field': 'result', 'pattern': '$RESULT'}},
                {'not': {'has': {'field': 'type_parameters', 'kind': 'type_parameter_list'}}}]}},
            {'id': 'constructor-binding', 'language': 'Go', 'rule': {
                'pattern': '$VARIABLE := $CONSTRUCTOR($$$ARGUMENTS)'}},
            {'id': 'block', 'language': 'Go', 'rule': {'kind': 'block'}},
            {'id': 'struct-type', 'language': 'Go', 'rule': {'all': [
                {'kind': 'type_spec'}, {'has': {'field': 'name', 'pattern': '$NAME'}},
                {'has': {'field': 'type', 'kind': 'struct_type'}}]}},
            {'id': 'call', 'language': 'Go', 'rule': {'kind': 'call_expression',
                'has': {'field': 'function', 'pattern': '$CALLEE'}}},
            {'id': 'import', 'language': 'Go', 'rule': {'kind': 'import_declaration'}},
            {'id': 'package', 'language': 'Go', 'rule': {'kind': 'package_clause'}},
            {'id': 'import-spec', 'language': 'Go', 'rule': {'kind': 'import_spec'}},
            {'id': 'declaration', 'language': 'Go', 'rule': {'any': [
                {'kind': kind} for kind in ('type_declaration', 'var_declaration', 'const_declaration')]}},
            {'id': 'declared-name', 'language': 'Go', 'rule': {'all': [
                {'any': [{'kind': kind} for kind in ('type_spec', 'var_spec', 'const_spec')]},
                {'has': {'field': 'name', 'pattern': '$NAME'}}]}},
            {'id': 'identifier', 'language': 'Go', 'rule': {'any': [
                {'kind': 'identifier'}, {'kind': 'type_identifier'}]}},
            {'id': 'parameter', 'language': 'Go', 'rule': {'any': [
                {'kind': 'parameter_declaration'}, {'kind': 'variadic_parameter_declaration'}]}},
            {'id': 'binding', 'language': 'Go', 'rule': {'all': [
                {'any': [{'kind': kind} for kind in ('short_var_declaration', 'assignment_statement', 'range_clause')]},
                {'has': {'field': 'left', 'pattern': '$BOUND'}}]}},
            {'id': 'if', 'language': 'Go', 'rule': {'kind': 'if_statement', 'all': [
                {'has': {'field': 'condition', 'pattern': '$CONDITION'}},
                {'has': {'field': 'consequence', 'pattern': '$THEN'}}]}},
            *[{'id': kind, 'language': 'Go', 'rule': {'kind': kind}} for kind in
              ('defer_statement', 'go_statement', 'func_literal', 'for_statement',
               'expression_switch_statement', 'expression_case', 'default_case')],
            {'id': 'error', 'language': 'Go', 'rule': {'kind': 'ERROR'}},
        ]
        with TemporaryDirectory(prefix='rlm-ast-') as directory:
            config = Path(directory) / 'sgconfig.yml'
            config.write_text('ruleDirs: []\n', encoding='utf-8')
            result = subprocess.run(['ast-grep', 'scan', '--config', str(config), '--stdin',
                '--inline-rules', '\n---\n'.join(json.dumps(r) for r in rules),
                '--json=compact', '--color=never', '--threads=1'],
                input=self.context['files'][path], text=True, capture_output=True, check=False,
                cwd=directory, timeout=30)
        if result.returncode != 0:
            raise ValueError('ast-grep failed; source output suppressed')
        matches = json.loads(result.stdout)
        if any(m['ruleId'] == 'error' for m in matches):
            raise ValueError(f'Go syntax errors in {path}; do not claim complete syntax coverage')
        self.syntax[path] = matches
        return matches

    def get_function(self, function_id: str) -> dict:
        """Return a complete function, imports and syntax calls. Go bounds are verified with ast-grep.

        Returns {id,path,start_line,end_line,code,verification,imports,calls}; code is a plain string.
        Calls inside closures are included; they do not establish execution order or runtime reachability.
        Other languages use the saved CodeGraph ranges and are labeled as such.
        """
        node = self.nodes[function_id]
        if node['kind'] not in ('function', 'method'):
            raise ValueError('Select a function or method ID')
        source = self.get_source_range(node['file_key'], node['startLine'], node['endLine'])
        if node['language'] != 'go':
            return {**source, 'id': function_id, 'verification': 'saved CodeGraph range'}
        matches = self.scan_go(node['file_key'])
        definitions = [m for m in matches if m['ruleId'] == 'definition'
                       and m['metaVariables']['single']['NAME']['text'] == node['name']
                       and m['range']['start']['line'] + 1 == node['startLine']
                       and m['range']['end']['line'] + 1 == node['endLine']]
        if len(definitions) != 1:
            raise ValueError('CodeGraph and AST function boundaries differ')
        bounds = definitions[0]['range']['byteOffset']
        calls = [m for m in matches if m['ruleId'] == 'call'
                 and bounds['start'] <= m['range']['byteOffset']['start']
                 and m['range']['byteOffset']['end'] <= bounds['end']]
        return {**source, 'id': function_id, 'verification': 'CodeGraph + ast-grep',
                'imports': [m['text'] for m in matches if m['ruleId'] == 'import'],
                'calls': [{'expression': m['metaVariables']['single']['CALLEE']['text'],
                           'line': m['range']['start']['line'] + 1} for m in calls]}

    def get_callers(self, function_id: str) -> dict:
        """Return graph callers plus Go syntax calls sharing the name, across the whole project.

        Syntax matches may resolve elsewhere. Empty matches do not prove dead code or non-execution.
        The Go scan excludes comments and declarations and includes build/test variants.
        """
        node = self.nodes[function_id]
        project_id = function_id.split('/')[0]
        graph = self.context['graphs'][project_id]
        edges = [e for e in graph['edges'] if e['target'] == node['id'] and e['kind'] == 'calls']
        candidates = []
        for path in sorted(self.context['files']):
            if not path.startswith(project_id + '/') or not path.endswith('.go'):
                continue
            for match in self.scan_go(path):
                if match['ruleId'] != 'call':
                    continue
                expression = match['metaVariables']['single']['CALLEE']['text']
                if expression.rsplit('.', 1)[-1] == node['name']:
                    candidates.append({'path': path, 'line': match['range']['start']['line'] + 1,
                                       'expression': expression, 'code': match['text']})
        return {'graph_edges': edges, 'syntax_candidates': candidates,
                'note': 'Candidate relationships only. Verify aliases/build conditions. No match means unverified activation.'}

    def get_callees(self, function_id: str) -> dict:
        """Return saved outgoing calls with original resolution metadata and scoped target IDs."""
        node = self.nodes[function_id]
        project_id = function_id.split('/')[0]
        edges = [e for e in self.context['graphs'][project_id]['edges']
                 if e['source'] == node['id'] and e['kind'] == 'calls']
        return {'edges': [{**e, 'target_id': f'{project_id}/{e["target"]}'} for e in edges],
                'note': 'Heuristic graph relationships. An absent edge is not evidence of absence.'}

    def get_sql_query(self, name: str, path_suffix: str) -> list[dict]:
        """Return a LIST of complete SQLC statements matching name and file suffix.

        Each entry has {id,path,start_line,end_line,code,query_name,kinds,relations}.
        Read entry['code']; its first line is entry['start_line']. Includes CTEs and all clauses.
        Empty means no parsed match. Do not shorten the statement or infer SQL from its name.
        """
        return [s for s in self.inventory['statements'].values()
                if s['query_name'] == name and s['path'].endswith(path_suffix)]

    def get_table(self, table_id: str) -> dict:
        """Read all CREATE declarations for an inventory ID, preserving their versions and schemas."""
        table = self.inventory['tables'][table_id]
        return {**table, 'declarations': [self.inventory['statements'][s] for s in table['declarations']]}


def check_answers(answers: list, questions: list[dict], files: dict[str, str]) -> dict:
    """Validate question accounting, answer vocabulary and exact source quotes, not semantics."""
    errors, citations = [], []
    expected = {q['id']: q for q in questions}
    if not isinstance(answers, list):
        return {'errors': ['Answers must be a list'], 'citations': []}
    ids = [a.get('id') for a in answers if isinstance(a, dict)]
    for qid in expected:
        if ids.count(qid) != 1:
            errors.append(f'{qid}: expected exactly one answer')
    for answer in answers:
        if not isinstance(answer, dict) or not isinstance(answer.get('id'), str) or answer['id'] not in expected:
            errors.append('Unexpected answer ID or non-object answer')
            continue
        qid = answer['id']
        if answer.get('value') not in [*expected[qid]['allowed_values'], 'unknown']:
            errors.append(f'{qid}: value outside the allowed vocabulary')
        evidence = answer.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            errors.append(f'{qid}: provide evidence even for an uncertainty')
            continue
        for citation in evidence:
            valid = isinstance(citation, dict) and isinstance(citation.get('path'), str)
            path = citation['path'] if valid else ''
            lines = files.get(path, '').splitlines()
            start = citation.get('start_line') if valid else None
            end = citation.get('end_line') if valid else None
            quote = citation.get('quote') if valid else None
            matched = (type(start) is int and type(end) is int and 1 <= start <= end <= len(lines)
                       and isinstance(quote, str) and bool(quote.strip())
                       and quote.strip() in '\n'.join(lines[start - 1:end]))
            citations.append({'question_id': qid, 'path': path, 'matches': bool(matched)})
            if not matched:
                errors.append(f'{qid}: citation does not match {path}:{start}-{end}')
    return {'errors': errors, 'citations': citations}
