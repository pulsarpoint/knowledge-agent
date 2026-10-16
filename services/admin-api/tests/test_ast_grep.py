"""Exercise structural searches using the real pinned ast-grep executable."""

import asyncio
import json

import pytest

from tool_helpers import tool
from access_helpers import with_access
from test_function_tools import context, invoke

ast_grep = tool('ast_grep')


@pytest.mark.parametrize(
    ('language', 'extension', 'source', 'pattern', 'expected'),
    [
        (
            'Go',
            'go',
            'package example\n\nfunc (s *Server) Handle(x int) {\n if x > 0 {\n  println("test-tool-secret")\n }\n}\nfunc other() {}\n',
            'func ($RECEIVER) Handle($$$ARGS) { $$$BODY }',
            'func (s *Server) Handle',
        ),
        (
            'Python',
            'py',
            'def process(value):\n    if value:\n        return {"key": value}\n    return None\n\ndef other():\n    pass\n',
            'def process($$$ARGS): $$$BODY',
            'def process(value):',
        ),
        (
            'TypeScript',
            'ts',
            'function process(value: number): number {\n if (value > 0) { return value; }\n return 0;\n}\nfunction other() {}\n',
            'function process($$$ARGS): $TYPE { $$$BODY }',
            'function process(value: number): number',
        ),
    ],
)
def test_full_declaration_ranges_and_redaction(context, tmp_path, language, extension, source, pattern, expected):
    path = tmp_path / 'code' / f'handler.{extension}'
    path.write_text(source)
    result = json.loads(asyncio.run(invoke(ast_grep, context, path=str(path), language=language, pattern=pattern)))
    assert len(result['matches']) == 1 and not result['truncated']
    match = result['matches'][0]
    assert match['path'] == str(path) and match['code'].startswith(expected)
    assert 'other' not in match['code'] and 'test-tool-secret' not in match['code']
    lines = source.splitlines()[match['start_line'] - 1 : match['end_line']]
    assert match['code'].splitlines()[0] == lines[0]
    assert match['code'].splitlines()[-1] == lines[-1]
    assert len(match['code'].splitlines()) == len(lines)
    assert path.read_text() == source


def test_name_rule_matches_functions_and_methods_and_reports_truncation(context, tmp_path):
    path = tmp_path / 'code' / 'handlers.go'
    path.write_text(
        'package example\nfunc Handle(x int) int { return x }\n'
        'func (s *Server) Handle() { if true { println("ok") } }\nfunc other() {}\n'
    )
    rule = json.dumps(
        {
            'any': [{'kind': 'function_declaration'}, {'kind': 'method_declaration'}],
            'has': {'field': 'name', 'regex': '^Handle$'},
        }
    )
    result = json.loads(asyncio.run(invoke(ast_grep, context, path=str(path), language='Go', rule=rule)))
    assert len(result['matches']) == 2 and not result['truncated']
    assert all('other' not in match['code'] for match in result['matches'])
    limited = json.loads(
        asyncio.run(invoke(ast_grep, context, path=str(path), language='Go', rule=rule, max_matches=1))
    )
    assert len(limited['matches']) == 1 and limited['truncated']
    assert limited['matches'][0]['code'] == result['matches'][0]['code']
    empty = json.loads(
        asyncio.run(invoke(ast_grep, context, path=str(path), language='Go', rule=rule.replace('Handle', 'Missing')))
    )
    assert empty['matches'] == [] and not empty['truncated']


def test_folder_scope_symlinks_disabled_access_and_project_config(context, tmp_path):
    code = tmp_path / 'code'
    (code / 'ok.py').write_text('def process():\n    return 1\n')
    # A selected source directory's parser config must not influence the tool.
    (code / 'sgconfig.yml').write_text('not: [valid yaml')
    outside = tmp_path / 'outside.py'
    outside.write_text('def process():\n    return "OUTSIDE"\n')
    (code / 'escape.py').symlink_to(outside)
    args = {'language': 'Python', 'pattern': 'def process($$$ARGS): $$$BODY'}
    result = json.loads(asyncio.run(invoke(ast_grep, context, path=str(code), **args)))
    assert len(result['matches']) == 1 and 'OUTSIDE' not in json.dumps(result)
    for path in (outside, code / 'escape.py'):
        assert 'outside' in asyncio.run(invoke(ast_grep, context, path=str(path), **args))
    assert 'absolute' in asyncio.run(invoke(ast_grep, context, path='ok.py', **args))
    context['config']['code']['enabled'] = False
    with_access(context['config'])  # code off at start: no code project in the run's access object
    assert asyncio.run(invoke(ast_grep, context, path=str(code), **args)) == 'This run has no code projects.'
    context['config']['code']['enabled'] = True
    with_access(context['config'])


@pytest.mark.parametrize(
    'arguments',
    [
        {},
        {'pattern': 'foo()', 'rule': '{}'},
        {'rule': 'not json'},
        {'rule': '[]'},
        {'pattern': ''},
        {'pattern': 'foo()', 'max_matches': 0},
        {'rule': '{"kind":"not_a_real_ast_node"}'},
    ],
)
def test_invalid_queries_report_errors(context, tmp_path, arguments):
    path = tmp_path / 'code' / 'source.go'
    path.write_text('package example\nfunc foo() {}\n')
    result = asyncio.run(invoke(ast_grep, context, path=str(path), language='Go', **arguments))
    assert 'An error occurred' in result
    assert path.read_text() == 'package example\nfunc foo() {}\n'
