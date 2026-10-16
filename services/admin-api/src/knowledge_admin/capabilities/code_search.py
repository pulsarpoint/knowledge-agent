"""Find named functions and inspect their syntax using the installed Tree-sitter engine."""

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from knowledge_admin.capabilities.ast_grep import scan_source
from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.files import _path
from knowledge_admin.sessions.activity import redact
from knowledge_admin.tools.registry import tool


def check_code_search(access: dict, **_) -> str | None:
    """None when allowed, else the refusal text. Which paths: inside the run's code projects (the work checks)."""
    if not access_rules.has_any(access, 'code_project'):
        return access_rules.refuse_none('code_project')
    return None


@tool
async def code_search(
    context: RunContext,
    path: Annotated[str, Field(description='Absolute source file or directory in a selected code project.')],
    language: Annotated[str, Field(description='Go, Python, JavaScript, TypeScript or Tsx (case-insensitive).')],
    name: Annotated[str, Field(description='Literal function/method name, not a regular expression.')],
    match: Annotated[Literal['exact', 'contains'], Field(description='Exact name or substring match.')] = 'exact',
    offset: Annotated[int, Field(description='Number of matching declarations to skip, starting at zero.')] = 0,
    limit: Annotated[int, Field(description='Maximum complete declarations returned, from 1 to 20.')] = 5,
) -> str:
    """Find functions/methods by name and return complete bodies, imports and call sites.

    Use this before reading whole files. Example: language="Go",
    name="SetPersonalLimits", path=<selected project folder>. Search a narrower
    file/folder to distinguish same-named symbols; receivers are included for Go.
    Supports Go, Python, JavaScript, TypeScript and Tsx. Includes named functions,
    methods, and JS/TS variables assigned arrow functions or function expressions.
    For classes, anonymous callbacks or custom patterns, use ast_grep.

    Calls are syntactic expressions inside each definition, including nested
    closures. They are NOT resolved targets or a guaranteed execution sequence.
    Follow package aliases using the returned imports; use code_graph for indexed
    callees and verify uncertain edges. Calls may include type conversions.
    Bodies are never shortened. Calls and imports use source locations, without
    repeating bodies/arguments. Results are sorted and paginated; follow next_offset.
    Git ignores apply to directory searches; symlinks are not followed. Empty
    results do not prove absence (other declaration forms/languages may need ast_grep).
    No build, code execution, LLM, graph generation, or dependencies are required.

    Use code_search to find a function or method by name and read its complete body, imports and call sites. Calls
    returned by code_search are syntax, including nested closures; resolve package/receiver targets before following
    them."""
    if refusal := check_code_search(context['config']['access'], path=path):
        return refusal
    if not name.strip() or len(name) > 300 or offset < 0 or not 1 <= limit <= 20:
        raise ValueError('Use a name up to 300 characters, nonnegative offset and limit from 1 to 20.')
    if match not in ('exact', 'contains'):
        raise ValueError('Match must be exact or contains.')
    languages = {'go': 'Go', 'python': 'Python', 'javascript': 'JavaScript', 'typescript': 'TypeScript', 'tsx': 'Tsx'}
    if language.lower() not in languages:
        raise ValueError('Use Go, Python, JavaScript, TypeScript or Tsx; use ast_grep for other languages.')
    language = languages[language.lower()]
    selected = _path(context, path)
    roots = [
        Path(folder).resolve()
        for project in access_rules.listing(context['config']['access'], 'code_project')
        for folder in project['folder_paths']
    ]
    if not any(selected.is_relative_to(root) for root in roots):
        raise ValueError('Choose a path inside a selected code project.')
    if not selected.is_file() and not selected.is_dir():
        raise ValueError('Choose a regular source file or directory.')

    expression = re.escape(name)
    if match == 'exact':
        expression = '^' + expression + '$'
    named = {'field': 'name', 'pattern': '$NAME', 'regex': expression}
    if language == 'Go':
        declarations = [
            {'kind': 'function_declaration', 'has': named},
            {
                'kind': 'method_declaration',
                'all': [{'has': named}, {'has': {'field': 'receiver', 'pattern': '$RECEIVER'}}],
            },
        ]
        imports = ['import_declaration', 'package_clause']
    elif language == 'Python':
        declarations = [{'kind': 'function_definition', 'has': named}]
        imports = ['import_statement', 'import_from_statement']
    else:
        declarations = [
            {'kind': kind, 'has': named}
            for kind in ('function_declaration', 'generator_function_declaration', 'method_definition')
        ]
        declarations.append(
            {
                'kind': 'variable_declarator',
                'all': [
                    {'has': named},
                    {
                        'has': {
                            'field': 'value',
                            'any': [
                                {'kind': 'arrow_function'},
                                {'kind': 'function_expression'},
                                {'kind': 'generator_function'},
                            ],
                        }
                    },
                ],
            }
        )
        imports = ['import_statement']
    definitions, warning = await scan_source(
        context,
        [selected],
        [{'id': f'definition-{index}', 'language': language, 'rule': rule} for index, rule in enumerate(declarations)],
    )
    definitions.sort(key=lambda item: (item['file'], item['range']['byteOffset']['start']))
    page = definitions[offset : offset + limit]
    # Parse only files on this page for context, not every file in the project.
    details = []
    if page:
        details, detail_warning = await scan_source(
            context,
            [Path(file) for file in sorted({item['file'] for item in page})],
            [
                {
                    'id': 'call',
                    'language': language,
                    'rule': {
                        'kind': 'call' if language == 'Python' else 'call_expression',
                        'has': {'field': 'function', 'pattern': '$CALLEE'},
                    },
                },
                *[{'id': f'context-{kind}', 'language': language, 'rule': {'kind': kind}} for kind in imports],
            ],
        )
        warning = '\n'.join(text for text in (warning, detail_warning) if text)
    results = []
    for definition in page:
        bounds = definition['range']['byteOffset']
        variables = definition['metaVariables']['single']
        calls = [
            item
            for item in details
            if item['ruleId'] == 'call'
            and item['file'] == definition['file']
            and bounds['start'] <= item['range']['byteOffset']['start']
            and item['range']['byteOffset']['end'] <= bounds['end']
        ]
        calls.sort(key=lambda item: (item['range']['byteOffset']['start'], item['range']['byteOffset']['end']))
        results.append(
            {
                'name': variables['NAME']['text'],
                'receiver': variables.get('RECEIVER', {}).get('text'),
                'path': definition['file'],
                'start_line': definition['range']['start']['line'] + 1,
                'end_line': definition['range']['end']['line'] + 1,
                'sha256': hashlib.sha256(definition['text'].encode()).hexdigest(),
                'code': definition['text'],
                'calls': [
                    {
                        'expression': item['metaVariables']['single']['CALLEE']['text'],
                        'line': item['range']['start']['line'] + 1,
                        'column': item['range']['start']['column'] + 1,
                    }
                    for item in calls
                ],
            }
        )
    file_context = [
        {
            'path': item['file'],
            'start_line': item['range']['start']['line'] + 1,
            'end_line': item['range']['end']['line'] + 1,
            'code': item['text'],
        }
        for item in details
        if item['ruleId'].startswith('context-')
    ]
    file_context.sort(key=lambda item: (item['path'], item['start_line']))
    result = {
        'matches': results,
        'file_context': file_context,
        'total_matches': len(definitions),
        'next_offset': offset + limit if offset + limit < len(definitions) else None,
        'warning': warning or None,
        'note': 'Tree-sitter syntax only. Calls include nested closures and are not resolved targets or execution order.',
    }
    return redact(json.dumps(result), context['credentials'].values())
