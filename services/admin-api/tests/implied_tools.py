"""The 0078 backfill rule — the tools a configuration's capabilities gave before tools were chosen per agent. The app
no longer needs it (every configuration has its tools); tests of that migration and of capability combinations do."""

from knowledge_admin.tools.catalog import CATALOG, _code, _documents, _files, _grounded, _ssh, item

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


def implied_tools(config):
    return {
        'items': [item(name, 'on' if name in DEFAULT_ON and DEFAULT_ON[name](config) else 'off') for name in CATALOG]
    }
