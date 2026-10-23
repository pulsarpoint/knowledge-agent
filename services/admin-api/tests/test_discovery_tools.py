"""Proposals are gone: documents and inventory are drafted. Nothing of them is left in the code, the catalog or the
routes."""

from pathlib import Path

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.tools.catalog import CATALOG
from test_discovery_runs import HEADERS, ssh_ready, start

REMOVED = ('propose_document', 'propose_service', 'discovery_proposals', 'discovery/proposals', '/proposals')


def test_no_proposal_tool_table_or_route_is_left_in_the_code():
    root = Path(__file__).resolve().parents[1]
    sources = [*(root / 'src').rglob('*'), *(root.parent / 'admin-ui/app').rglob('*')]
    found = [
        f'{path}: {name}'
        for path in sources
        if path.is_file() and path.suffix in {'.py', '.json', '.ts', '.tsx'}
        for name in REMOVED
        if name in path.read_text(errors='ignore')
    ]
    assert found == []


def test_the_catalog_has_no_proposal_tool():
    assert not [name for name in CATALOG if 'propos' in name]


def test_the_proposal_routes_are_gone(world):
    ssh_ready(world)
    sid = start(world).json()['id']
    for method, url in (
        ('get', f'/api/discovery/{sid}/proposals'),
        ('get', '/api/discovery/proposals?tag=servers/x'),
        ('post', f'/api/discovery/proposals/{sid}/accept'),
        ('post', f'/api/discovery/proposals/{sid}/reject'),
    ):
        assert getattr(world.client, method)(url, headers=HEADERS).status_code in (404, 405), url
