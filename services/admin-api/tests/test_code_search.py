"""Check actual Tree-sitter results, scope checks and SDK registration."""

import asyncio
import hashlib
import json

import pytest

from tool_helpers import tool
from test_function_tools import context, invoke

code_search = tool('code_search')


def test_go_methods_imports_calls_and_complete_bodies(context, tmp_path):
    path = tmp_path / 'code' / 'handler.go'
    source = """package controller
import limits "example.test/personal_limits"
// func Handle() { fake() }
func (c *Ctrl) Handle() {
    text := "notAFunction()"
    limits.GetPersonalLimits()
    c.GetPersonalLimits()
    defer func() { rollback() }()
    println(text, "test-tool-secret")
}
func Handle() { another() }
"""
    path.write_text(source, encoding='utf-8')
    args = {'path': str(path), 'language': 'go', 'name': 'Handle'}
    result = json.loads(asyncio.run(invoke(code_search, context, **args)))
    assert result['total_matches'] == 2 and result['next_offset'] is None
    method, function = result['matches']
    assert method['receiver'] == '(c *Ctrl)' and function['receiver'] is None
    assert method['start_line'] == 4 and method['end_line'] == 10
    assert method['code'].endswith('\n}') and 'test-tool-secret' not in method['code']
    assert 'another' not in method['code']
    calls = {call['expression']: call['line'] for call in method['calls']}
    assert calls['limits.GetPersonalLimits'] == 6 and calls['c.GetPersonalLimits'] == 7
    assert 'rollback' in calls and 'notAFunction' not in calls and 'fake' not in calls
    assert 'example.test/personal_limits' in json.dumps(result['file_context'])
    assert 'package controller' in json.dumps(result['file_context'])
    assert path.read_text(encoding='utf-8') == source


@pytest.mark.parametrize(
    ('language', 'extension', 'source', 'expected'),
    [
        (
            'Python',
            'py',
            'import storage as db\nclass Service:\n    async def save(self):\n        return db.insert(1)\n',
            'db.insert',
        ),
        (
            'JavaScript',
            'js',
            'import db from "db";\nconst save = async (x) => { return db.insert(x); };\n',
            'db.insert',
        ),
        (
            'TypeScript',
            'ts',
            'import db from "db";\nclass Service { save(x: number): number { return db.insert(x); } }\n',
            'db.insert',
        ),
        ('Tsx', 'tsx', 'import React from "react";\nfunction save() { return <div>{load()}</div>; }\n', 'load'),
    ],
)
def test_languages(context, tmp_path, language, extension, source, expected):
    path = tmp_path / 'code' / f'source.{extension}'
    path.write_text(source, encoding='utf-8')
    result = json.loads(asyncio.run(invoke(code_search, context, path=str(path), language=language, name='save')))
    assert result['total_matches'] == 1
    declaration = result['matches'][0]
    assert expected in {call['expression'] for call in declaration['calls']}
    assert declaration['sha256'] == hashlib.sha256(declaration['code'].encode()).hexdigest()
    assert result['file_context']


def test_literal_names_stable_pagination_and_scope(context, tmp_path):
    root = tmp_path / 'code'
    for file, name in [('b.go', 'HandleB'), ('a.go', 'HandleA')]:
        (root / file).write_text(f'package main\nfunc {name}() {{}}\n', encoding='utf-8')
    args = {'path': str(root), 'language': 'Go', 'name': 'Handle', 'match': 'contains', 'limit': 1}
    first = json.loads(asyncio.run(invoke(code_search, context, **args)))
    second = json.loads(asyncio.run(invoke(code_search, context, **args, offset=first['next_offset'])))
    assert first['matches'][0]['name'] == 'HandleA' and second['matches'][0]['name'] == 'HandleB'
    assert second['next_offset'] is None and first['total_matches'] == 2
    literal = json.loads(asyncio.run(invoke(code_search, context, **(args | {'name': '.*'}))))
    assert literal['matches'] == []
    outside = tmp_path / 'outside.go'
    outside.write_text('package other\nfunc HandleOutside() {}\n', encoding='utf-8')
    (root / 'escape.go').symlink_to(outside)
    (root / 'sgconfig.yml').write_text('invalid: [yaml', encoding='utf-8')
    assert json.loads(asyncio.run(invoke(code_search, context, **args))) == first
    for selected in [outside, root / 'escape.go', tmp_path / 'documents', tmp_path / 'workspace']:
        assert 'error' in asyncio.run(invoke(code_search, context, **(args | {'path': str(selected)}))).lower()
    context['config']['code']['enabled'] = False
    assert 'disabled' in asyncio.run(invoke(code_search, context, **args))
    context['config']['code']['enabled'] = True
    context['config']['enabled'] = False
    assert 'disabled' in asyncio.run(invoke(code_search, context, **args))


@pytest.mark.parametrize(
    'extra', [{'name': ''}, {'limit': 0}, {'offset': -1}, {'language': 'invalid'}, {'match': 'regex'}]
)
def test_invalid_arguments(context, tmp_path, extra):
    args = {'path': str(tmp_path / 'code'), 'language': 'Go', 'name': 'Handle'} | extra
    assert 'error' in asyncio.run(invoke(code_search, context, **args)).lower()
