"""propose_document: checked and stored as a pending proposal of the run. Service proposals are gone: inventory
findings are drafts."""

import asyncio
from pathlib import Path

import pydantic
import pytest

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.capabilities.discovery import NOT_DISCOVERY
from knowledge_admin.tools.catalog import CATALOG
from test_discovery_runs import config_of, ssh_ready, start


@pytest.fixture
def run(world):
    ssh_ready(world)
    sid = start(world).json()['id']
    return world, sid, config_of(world, sid)


def propose(config, tool, **arguments):
    spec = CATALOG[tool]
    return asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))


def proposals(world, sid):
    with world.store.connect() as conn:
        return conn.execute(
            'SELECT * FROM ka.discovery_proposals WHERE session_id=%s ORDER BY number', (sid,)
        ).fetchall()


def test_documents_attach_to_the_server_or_a_recorded_service(run):
    world, sid, config = run
    assert (
        propose(config, 'propose_document', title='Web-1 overview', markdown='# web-1')
        == 'Proposal 1: new version of document "Web-1 overview" about server web-1.'
    )
    assert (
        propose(config, 'propose_document', title='Runbook', markdown='# nginx', about_service='nginx')
        == 'Proposal 2: new document "Runbook" about service nginx.'
    )
    with pytest.raises(ValueError, match='No service named mysql. Save it first'):
        propose(config, 'propose_document', title='MySQL', markdown='# x', about_service='mysql')
    rows = proposals(world, sid)
    assert rows[0]['document_id'] is not None and rows[0]['service_id'] is None
    assert rows[1]['service_id'] == world.nginx and rows[1]['document_id'] is None


def test_proposal_tools_work_only_in_runs_started_from_a_resource_and_are_catalog_tools(run):
    world, sid, config = run
    assert NOT_DISCOVERY == 'The proposal tools work only in a run started from a server, service or environment.'
    with pytest.raises(ValueError, match=NOT_DISCOVERY):
        propose(
            {key: value for key, value in config.items() if key != 'session_id'},
            'propose_document',
            title='T',
            markdown='# t',
        )
    with world.store.connect() as conn:  # another kind of run: its session is not a resource run
        other = conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,document_name,folder,agent_config)
            SELECT gen_random_uuid(),project_id,NULL,actor_id,model_id,'Docs','{}','{}','documents','Docs','docs','{}'
            FROM ka.agent_sessions WHERE id=%s RETURNING id""",
            (sid,),
        ).fetchone()['id']
    with pytest.raises(ValueError, match=NOT_DISCOVERY):
        propose({**config, 'session_id': str(other)}, 'propose_document', title='T', markdown='# t')
    assert proposals(world, sid) == []
    assert {'propose_document', 'get_resource', 'get_document'} <= set(CATALOG)
    assert 'propose_service' not in CATALOG


def test_repeated_documents_replace_their_pending_proposal_and_services_stay_apart(run):
    world, sid, config = run
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# one')
        == 'Proposal 1: new document "Notes" about server web-1.'
    )
    assert (
        propose(config, 'propose_document', title='notes', markdown='# two')
        == 'Proposal 1: new document "notes" about server web-1 (replaces your earlier proposal).'
    )
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# nginx', about_service='nginx')
        == 'Proposal 2: new document "Notes" about service nginx.'
    )
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# nginx 2', about_service='NGINX')
        == 'Proposal 2: new document "Notes" about service nginx (replaces your earlier proposal).'
    )
    rows = proposals(world, sid)
    assert [row['number'] for row in rows] == [1, 2]
    assert [row['revision'] for row in rows] == [2, 2]
    assert [row['payload']['markdown'] for row in rows] == ['# two', '# nginx 2']


def test_a_decided_proposal_is_never_overwritten_by_a_late_repeat(run):
    world, sid, config = run
    propose(config, 'propose_document', title='Notes', markdown='# one')
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.discovery_proposals SET status='rejected',decided_at=now() WHERE session_id=%s", (sid,))
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# late')
        == 'Proposal 2: new document "Notes" about server web-1.'
    )
    rows = proposals(world, sid)
    assert [row['status'] for row in rows] == ['rejected', 'pending']
    assert rows[0]['payload']['markdown'] == '# one' and rows[0]['revision'] == 1


def test_the_update_itself_rechecks_the_status(run, monkeypatch):
    from knowledge_admin.capabilities import discovery as tools

    world, sid, config = run
    propose(config, 'propose_document', title='Notes', markdown='# one')
    original = tools._save

    def decided_first(conn, session, payload, existing, **kwargs):
        conn.execute(
            "UPDATE ka.discovery_proposals SET status='rejected',decided_at=now() WHERE id=%s", (existing['id'],)
        )
        return original(conn, session, payload, existing, **kwargs)

    monkeypatch.setattr(tools, '_save', decided_first)
    assert (
        propose(config, 'propose_document', title='Notes', markdown='# late')
        == 'Proposal 2: new document "Notes" about server web-1.'
    )
    rows = proposals(world, sid)
    assert [row['status'] for row in rows] == ['rejected', 'pending']
    assert rows[0]['payload']['markdown'] == '# one'


def test_text_with_null_characters_is_refused_in_every_proposal_field():
    with pytest.raises(pydantic.ValidationError, match='Text cannot contain null characters.'):
        CATALOG['propose_document'].arguments.model_validate({'title': 'T', 'markdown': 'a\x00'})
    with pytest.raises(pydantic.ValidationError, match='Text cannot contain null characters.'):
        CATALOG['propose_document'].arguments.model_validate({'title': 'T', 'markdown': 'a', 'about_service': '\x00'})


def test_there_is_no_service_proposal_tool_left_in_the_code():
    root = Path(__file__).resolve().parents[1]
    sources = [*(root / 'src').rglob('*'), *(root.parent / 'admin-ui/app').rglob('*')]
    found = [
        str(path)
        for path in sources
        if path.is_file()
        and path.suffix in {'.py', '.json', '.ts', '.tsx'}
        and 'propose_service' in path.read_text(errors='ignore')
    ]
    assert found == []
