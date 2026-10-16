"""Read complete syntax nodes with the ast-grep CLI in selected source folders."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.files import _path
from knowledge_admin.sessions.activity import redact
from knowledge_admin.tools.registry import tool


async def scan_source(context: RunContext, paths: list[Path], rules: list[dict]) -> tuple[list[dict], str]:
    """Run read-only Tree-sitter queries without loading a project's parser plugins."""
    inline = '\n---\n'.join(json.dumps(rule) for rule in rules)
    with TemporaryDirectory(prefix='knowledge-ast-grep-') as directory:
        config = Path(directory) / 'sgconfig.yml'
        config.write_text('ruleDirs: []\n', encoding='utf-8')
        process = await asyncio.create_subprocess_exec(
            'ast-grep',
            'scan',
            '--config',
            str(config),
            '--inline-rules',
            inline,
            '--json=compact',
            '--color=never',
            '--threads=1',
            '--',
            *map(str, paths),
            cwd=directory,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(30):
                stdout, stderr = await process.communicate()
        except TimeoutError:
            raise ValueError('Structural search timed out. Narrow the source folder or query.') from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()
    error = redact(stderr.decode('utf-8', errors='replace').strip(), context['credentials'].values())
    if process.returncode != 0:
        raise ValueError(error or f'ast-grep failed with exit code {process.returncode}.')
    matches = json.loads(stdout)
    for match in matches:
        match['file'] = str(_path(context, match['file']))
    return matches, error


def check_ast_grep(access: dict, **_) -> str | None:
    """None when allowed, else the refusal text. Which paths: inside the run's code projects (the work checks)."""
    if not access_rules.has_any(access, 'code_project'):
        return access_rules.refuse_none('code_project')
    return None


@tool
async def ast_grep(
    context: RunContext,
    path: Annotated[str, Field(description='Absolute source file or folder within the configured roots.')],
    language: Annotated[
        str, Field(description='Built-in ast-grep language, such as Go, Python, JavaScript or TypeScript.')
    ],
    pattern: Annotated[str | None, Field(description='Native ast-grep code pattern. Omit when supplying rule.')] = None,
    rule: Annotated[
        str | None,
        Field(
            description='JSON rule object, e.g. {"kind":"function_declaration"}; omit with pattern. Supply only the rule object, not a full config, language, fix or rewrite.'
        ),
    ] = None,
    max_matches: Annotated[int, Field(description='Maximum complete matches to return, from 1 to 100.')] = 20,
) -> str:
    """Search source structure with ast-grep and return complete matched code and line ranges.

    Use for whole functions, methods, classes, and structural patterns. Use
    search_files for literal text and code_graph for callers/callees. This tool
    parses syntax, not semantic type resolution; verify receiver/package matches.
    Choose a source file or narrow folder from the configured code projects.
    Git ignores apply to directory searches; symlinks are not followed.

    Supply exactly one native pattern or a native rule object encoded as JSON.
    $NAME matches one syntax node; $$$NAME matches a sequence. Patterns depend on
    language and declaration shape; a method pattern need not match a free function.

    Go method body: language="Go",
    pattern="func ($RECEIVER) SetPersonalLimits($$$ARGS) { $$$BODY }".
    Go functions or methods by name, regardless of signature: language="Go",
    rule='{"any":[{"kind":"function_declaration"},{"kind":"method_declaration"}],
    "has":{"field":"name","regex":"^SetPersonalLimits$"}}'.
    Python function: language="Python", pattern="def process($$$ARGS): $$$BODY".

    Results contain the complete matched node once, including its signature/body
    when a whole declaration is matched; adjacent doc comments are not included.
    Use read_file for surrounding comments/imports or other source ranges. If
    truncated, narrow the folder/pattern or request more matches. Empty results
    do not prove a symbol is absent: check language, pattern shape, and ignores.

    Use ast_grep for custom structural patterns, classes and other declaration forms."""
    if refusal := check_ast_grep(context['config']['access'], path=path):
        return refusal
    if (pattern is None) == (rule is None):
        raise ValueError('Supply exactly one pattern or rule.')
    query = pattern if pattern is not None else rule
    if not query.strip() or len(query) > 20000 or not language.strip() or not 1 <= max_matches <= 100:
        raise ValueError('Provide a language, a query up to 20000 characters and max_matches from 1 to 100.')
    try:
        matcher = {'pattern': pattern} if pattern is not None else json.loads(rule)
    except json.JSONDecodeError:
        raise ValueError('The rule must be a JSON object using native ast-grep rule syntax.') from None
    if not isinstance(matcher, dict):
        raise ValueError('The rule must be a JSON object.')
    selected = _path(context, path)
    if not selected.is_file() and not selected.is_dir():
        raise ValueError('Choose a regular source file or directory.')
    matches, error = await scan_source(
        context, [selected], [{'id': 'agent-source-search', 'language': language, 'rule': matcher}]
    )
    result = {
        'matches': [
            {
                'path': str(_path(context, match['file'])),
                'start_line': match['range']['start']['line'] + 1,
                'end_line': match['range']['end']['line'] + 1,
                'code': match['text'],
            }
            for match in matches[:max_matches]
        ],
        'truncated': len(matches) > max_matches,
        'warning': error or None,
    }
    return redact(json.dumps(result), context['credentials'].values())
