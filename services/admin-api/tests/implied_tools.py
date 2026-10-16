"""The 0078 backfill rule — the tools a configuration's capabilities gave before tools were chosen per agent, in the
tools section configurations had until 0115. The app no longer needs it (every run gets every tool); the tests of
the migrations that wrote ka.agent_tools rows do."""

from knowledge_admin.tools.catalog import CATALOG


def _grounded(config):
    return bool(config['documents']['enabled'] and config['documents'].get('grounded', False))


def _files(config):
    return bool(config['code']['enabled'] or config['documents']['enabled'] and not _grounded(config))


def _code(config):
    return bool(config['code']['enabled'])


def _documents(config):
    return bool(config['documents']['enabled'])


def _ssh(config):
    return bool(config['ssh']['enabled'])


DEFAULT_ON = {
    'list_files': _files,
    'read_file': _files,
    'search_files': _files,
    'shell_run': _files,
    'code_search': _code,
    'ast_grep': _code,
    'code_graph': _code,
    'search_documents': _documents,
    'read_document': _documents,
    'report_not_covered': _grounded,
    'ssh_run': _ssh,
}


def item(name, mode, usage=None, group=None, subagent_allowed=True):
    """One entry of the old tools section."""
    return {
        'tool': name,
        'group': group or CATALOG[name].group,
        'mode': mode,
        'usage': usage,
        'subagent_allowed': subagent_allowed,
    }


def implied_tools(config):
    return {
        'items': [item(name, 'on' if name in DEFAULT_ON and DEFAULT_ON[name](config) else 'off') for name in CATALOG]
    }
