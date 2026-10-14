"""Read complete syntax nodes with the ast-grep CLI in selected source folders."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from knowledge_admin.sessions.activity import redact
from knowledge_admin.capabilities.context import RunContext, require_given
from knowledge_admin.capabilities.files import _path


async def scan_source(context: RunContext, paths: list[Path], rules: list[dict]) -> tuple[list[dict], str]:
    """Run read-only Tree-sitter queries without loading a project's parser plugins."""
    inline = '\n---\n'.join(json.dumps(rule) for rule in rules)
    with TemporaryDirectory(prefix='knowledge-ast-grep-') as directory:
        config = Path(directory) / 'sgconfig.yml'
        config.write_text('ruleDirs: []\n', encoding='utf-8')
        process = await asyncio.create_subprocess_exec(
            'ast-grep', 'scan', '--config', str(config), '--inline-rules', inline,
            '--json=compact', '--color=never', '--threads=1', '--', *map(str, paths), cwd=directory,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
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


async def ast_grep(context: RunContext, path: str, language: str,
                   pattern: str | None = None, rule: str | None = None,
                   max_matches: int = 20) -> str:
    if not context['config']['code']['enabled']:
        raise ValueError('Code access is disabled.')
    require_given(context['config'], 'code')
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
    matches, error = await scan_source(context, [selected], [
        {'id': 'agent-source-search', 'language': language, 'rule': matcher}])
    result = {'matches': [{'path': str(_path(context, match['file'])),
        'start_line': match['range']['start']['line'] + 1,
        'end_line': match['range']['end']['line'] + 1,
        'code': match['text']} for match in matches[:max_matches]],
        'truncated': len(matches) > max_matches, 'warning': error or None}
    return redact(json.dumps(result), context['credentials'].values())
