"""Implementations live in knowledge_admin.capabilities without the Agents SDK; tools/ only wraps them."""

import ast
from pathlib import Path

import knowledge_admin

PACKAGE = Path(knowledge_admin.__file__).resolve().parent
CAPABILITIES = PACKAGE / 'capabilities'


def imported_modules(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_capabilities_hold_the_implementations_without_the_agents_sdk():
    expected = {
        '__init__.py',
        'context.py',
        'files.py',
        'documents.py',
        'ssh.py',
        'code_graph.py',
        'code_search.py',
        'ast_grep.py',
        'inventory.py',
        'discovery.py',
        'document_tools.py',
        'mcp.py',
        'shell/__init__.py',
        'shell/client.py',
        'shell/worker.py',
    }
    assert {str(path.relative_to(CAPABILITIES)) for path in CAPABILITIES.rglob('*.py')} == expected
    for path in CAPABILITIES.rglob('*.py'):
        assert not {name for name in imported_modules(path) if name == 'agents' or name.startswith('agents.')}, path
    assert (CAPABILITIES / '__init__.py').read_text().strip().startswith('"""')
    assert imported_modules(CAPABILITIES / '__init__.py') == set()
    assert not (PACKAGE / 'ssh.py').exists() and not (PACKAGE / 'shell_worker.py').exists()


def test_the_shell_worker_imports_without_the_application_config():
    worker = imported_modules(CAPABILITIES / 'shell/worker.py')
    assert not {name for name in worker if name.startswith('knowledge_admin')}


def test_tools_only_wrap_capabilities():
    for name in ('catalog',):
        source = (PACKAGE / 'tools' / f'{name}.py').read_text(encoding='utf-8')
        assert 'knowledge_admin.capabilities' in source, name
        for implementation in ('asyncio.create_subprocess_exec', 'httpx.AsyncClient(', 'SSHConnection('):
            assert implementation not in source, (name, implementation)
