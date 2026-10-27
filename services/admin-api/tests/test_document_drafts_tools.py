"""Agents draft documents in the real documents with new_document, update_document and edit_document; list_documents
lists a resource's documents with the run's drafts. Usable = readable: a document in the run's access or linked to a
resource in it. No Content editor role and no editable flag: old access objects that still carry them load and run."""

import json
import re
import subprocess
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from access_helpers import entry, with_access
from document_tools_world import call, rev, run_drafts  # noqa: F401  (fixture)
from drafts_world import API, docs, make_document, promote  # noqa: F401  (fixture)
from knowledge_admin import access as access_rules
from knowledge_admin.agents.runner import instructions_and_tools
from knowledge_admin.capabilities.document_tools import DRAFT_NOTE, DUPLICATE_TITLE
from knowledge_admin.knowledge_agents.subagents import subagent_config
from test_repositories import HEADERS
from test_generations import sessions  # noqa: F401  (fixture)
from test_sessions_saved_agent import agent, start  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)
from test_tool_catalog import config as catalog_config

ROOT = Path(__file__).resolve().parents[3]
CREATED = re.compile(r'\(id ([0-9a-f-]{36})\)')


def inventory(rev) -> dict:
    """An environment Production with the service api deployed on the server web-1 in it."""
    ids = {'environment': uuid4(), 'service': uuid4()}
    with rev.docs.store.connect() as conn:
        project = rev.docs.project
        conn.execute(
            'INSERT INTO ka.environments(id,project_id,name) VALUES (%s,%s,%s)',
            (ids['environment'], project, 'Production'),
        )
        conn.execute('INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,%s)', (ids['service'], project, 'api'))
        ids['server'] = conn.execute(
            """INSERT INTO ka.servers(project_id,name,hostname,username,enabled) VALUES (%s,'web-1','10.0.0.7','ops',false)
            RETURNING id""",
            (project,),
        ).fetchone()['id']
    return {kind: str(value) for kind, value in ids.items()}


def environment_chat(rev, found: dict) -> None:
    """The run is a chat on the environment: its service and server come with it."""
    rev.config['access'] = {
        'resources': [
            {**entry('environment', found['environment'], 'Production'), 'source': 'given'},
            {**entry('service', found['service'], 'api'), 'source': f'via environment {found["environment"]}'},
            {**entry('server', found['server'], 'web-1'), 'source': f'via environment {found["environment"]}'},
        ]
    }


def created_id(reply: str) -> str:
    return CREATED.search(reply).group(1)


def links(rev, document_id: str) -> list[tuple[str, str, str]]:
    detail = rev.docs.client.get(f'{API}/documents/{document_id}', headers=HEADERS).json()
    return sorted((item['type'], str(item['id']), item['connection']) for item in detail['resource_links'])


def test_an_environment_chat_drafts_a_new_document_for_its_service_and_edits_it(rev):
    """Review focus 1: new_document, then edit_document twice: one draft, built for the service, in its drafts."""
    found = inventory(rev)
    environment_chat(rev, found)
    reply = call(
        rev,
        'new_document',
        resource={'type': 'service', 'name': 'API'},
        title='API runbook',
        content='# API runbook\n\nStart it.\n\nStop it.\n',
        tags=['runbook'],
    )
    assert reply.endswith(DRAFT_NOTE) and 'built for service api' in reply
    document = created_id(reply)
    first = call(rev, 'edit_document', document_id=document, old_text='Start it.', new_text='Start it with make.')
    second = call(rev, 'edit_document', document_id=document, old_text='Stop it.', new_text='Stop it with make.')
    assert first == second == f'Saved draft (5 lines). {DRAFT_NOTE}'
    [draft] = run_drafts(rev)
    assert (str(draft['id']), draft['document_id'], draft['title']) == (document, None, 'API runbook')
    assert draft['markdown'] == '# API runbook\n\nStart it with make.\n\nStop it with make.\n'
    listed = rev.docs.client.get(f'{API}/drafts?service_id={found["service"]}', headers=HEADERS).json()['drafts']
    assert [(str(item['id']), item['published'], item['slug']) for item in listed] == [
        (document, False, 'api-api-runbook')
    ]
    read = json.loads(call(rev, 'get_document', document_id=document))
    assert (read['reads'], read['title']) == ('your draft', 'API runbook')
    # published, the draft's id is the document's id: later updates draft the live document
    detail = rev.docs.client.get(f'{API}/drafts/{document}', headers=HEADERS).json()
    assert promote(rev.docs, detail).status_code == 200
    assert links(rev, document) == [('service', found['service'], 'built_for')]
    assert call(rev, 'update_document', document_id=document, content='# API runbook\n\nv2\n').startswith(
        'Saved draft (3 lines).'
    )
    [after] = run_drafts(rev)
    assert str(after['document_id']) == document


def test_a_title_already_linked_to_the_resource_is_refused_with_its_id(rev):
    found = inventory(rev)
    environment_chat(rev, found)
    resource = {'type': 'server', 'id': found['server']}
    document = created_id(call(rev, 'new_document', resource=resource, title='Disks', content='# Disks'))
    again = call(rev, 'new_document', resource=resource, title='disks', content='# Disks again')
    assert again == DUPLICATE_TITLE.format(title='disks', type='server', id=document)
    assert again == (
        f'A document titled "disks" is already linked to this server ({document}); update it with update_document '
        'instead.'
    )
    live = make_document(rev.docs, 'backups', 'Backups')
    with rev.docs.store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.document_resources(project_id,document_id,server_id,connection,added_how)
            VALUES (%s,%s,%s,'linked','person')""",
            (rev.docs.project, live, found['server']),
        )
    assert call(rev, 'new_document', resource=resource, title='Backups', content='# B') == DUPLICATE_TITLE.format(
        title='Backups', type='server', id=live
    )
    assert len(run_drafts(rev)) == 1


def test_update_document_links_a_resource_only_when_the_draft_is_published(rev):
    """Review focus 2: the link is stored with the draft and added to the document on publish."""
    found = inventory(rev)
    rev.config['access']['resources'].append({**entry('server', found['server'], 'web-1'), 'source': 'given'})
    reply = call(
        rev,
        'update_document',
        document_id=str(rev.docs.runbook),
        content='# Runbook\n\nOn web-1.\n',
        resource={'type': 'server', 'id': found['server']},
    )
    assert reply == (
        f'Saved draft (3 lines). It is linked to server web-1 when a person publishes the draft. {DRAFT_NOTE}'
    )
    assert links(rev, rev.docs.runbook) == []
    [draft] = run_drafts(rev)
    detail = rev.docs.client.get(f'{API}/drafts/{draft["id"]}', headers=HEADERS).json()
    assert promote(rev.docs, detail).status_code == 200
    assert links(rev, rev.docs.runbook) == [('server', found['server'], 'linked')]
    # already linked: nothing to add
    again = call(
        rev,
        'update_document',
        document_id=str(rev.docs.runbook),
        content='# Runbook\n\nStill web-1.\n',
        resource={'type': 'server', 'name': 'web-1'},
    )
    assert again.endswith(f'version 3. It is already linked to server web-1. {DRAFT_NOTE}')


def test_a_resource_outside_the_run_is_refused_for_update_and_new(rev):
    found = inventory(rev)
    server = {'type': 'server', 'id': found['server']}
    refused = 'This run has no servers.'
    assert call(rev, 'update_document', document_id=str(rev.docs.runbook), content='# R', resource=server) == refused
    assert call(rev, 'new_document', resource=server, title='Disks', content='# Disks') == refused
    assert run_drafts(rev) == []


def test_a_document_page_run_without_the_editable_flag_drafts_its_document(rev):
    """Review focus 3: the Ask agent run's document is in its access as a plain entry; no role."""
    with_access(rev.config, [entry('document', rev.docs.runbook, 'Runbook')])
    assert rev.config['access'] == {
        'resources': [{**entry('document', rev.docs.runbook, 'Runbook'), 'source': 'given'}]
    }
    assert call(rev, 'update_document', document_id=str(rev.docs.runbook), content='# Runbook\n\nNew.') == (
        f'Saved draft (3 lines). {DRAFT_NOTE}'
    )
    assert call(rev, 'edit_document', document_id=str(rev.docs.runbook), old_text='New.', new_text='Newer.') == (
        f'Saved draft (3 lines). {DRAFT_NOTE}'
    )
    assert len(run_drafts(rev)) == 1


def test_a_document_neither_given_nor_linked_is_refused_with_the_documents_the_run_may_use(rev):
    """Review focus 4."""
    found = inventory(rev)
    other = make_document(rev.docs, 'other', 'Other')
    rev.config['access'] = {
        'resources': [
            {**entry('document', other, 'Other'), 'source': 'given'},
            {**entry('server', found['server'], 'web-1'), 'source': 'given'},
        ]
    }
    refusal = (
        f'This document is not available in this run. Documents you may use: Other ({other}). Documents linked to a '
        'server, service or environment in this run are usable too: list_documents lists them.'
    )
    for tool, arguments in (
        ('update_document', {'content': '# R'}),
        ('edit_document', {'old_text': 'Runbook', 'new_text': 'R'}),
        ('get_document', {}),
    ):
        assert call(rev, tool, document_id=str(rev.docs.runbook), **arguments) == refusal
    assert run_drafts(rev) == []


def test_list_documents_lists_a_resources_documents_with_the_runs_drafts(rev):
    found = inventory(rev)
    environment_chat(rev, found)
    guide = make_document(rev.docs, 'api-guide', 'API guide')
    with rev.docs.store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.document_resources(project_id,document_id,service_id,connection,added_how)
            VALUES (%s,%s,%s,'linked','person')""",
            (rev.docs.project, guide, found['service']),
        )
    assert json.loads(call(rev, 'list_documents', resource={'type': 'service', 'id': found['service']})) == {
        'documents': [
            {
                'id': guide,
                'title': 'API guide',
                'description_short': '',
                'tags': [],
                'published': True,
                'your_draft': False,
            }
        ]
    }
    call(rev, 'edit_document', document_id=guide, old_text='# API guide', new_text='# API guide\n\nMore.')
    new = created_id(
        call(rev, 'new_document', resource={'type': 'service', 'id': found['service']}, title='Alerts', content='# A')
    )
    listed = json.loads(call(rev, 'list_documents', resource={'type': 'service', 'name': 'api'}))['documents']
    assert [(item['id'], item['published'], item['your_draft']) for item in listed] == [
        (guide, True, True),
        (new, False, True),
    ]
    assert call(rev, 'list_documents', resource={'type': 'service', 'name': 'nope'}) == (
        'This service is not available in this run.'
    )


def test_old_access_objects_with_the_role_and_editable_load_and_run(rev):
    """Review focus 5: the stored keys are ignored."""
    old = {
        'resources': [{**entry('document', rev.docs.runbook, 'Runbook', editable=False), 'source': 'given'}],
        'roles': {'content_editor': False},
    }
    rev.config['access'] = old
    assert call(rev, 'update_document', document_id=str(rev.docs.runbook), content='# R').startswith('Saved draft')
    narrowed = access_rules.narrow(old, [{'type': 'document', 'id': str(rev.docs.runbook), 'editable': True}])
    assert narrowed == {'resources': old['resources']}


def test_a_saved_agent_whose_stored_access_has_the_role_and_editable_starts(agent, ask):
    with ask.s.store.connect() as conn:
        conn.execute(
            'UPDATE ka.agents SET access=%s WHERE id=%s',
            (
                Jsonb(
                    {
                        'resources': [
                            {'type': 'server', 'id': agent.web},
                            {'type': 'document', 'id': str(ask.document['id']), 'editable': True},
                        ],
                        'roles': {'content_editor': True},
                    }
                ),
                agent.id,
            ),
        )
    response = start(agent.client, '', agent_id=agent.id)
    assert response.status_code == 201, response.text
    access = response.json()['config']['access']
    assert 'roles' not in access and all('editable' not in item for item in access['resources'])
    assert {'document', 'server'} <= {item['type'] for item in access['resources']}


def test_subagents_and_experts_keep_the_read_tools_only():
    parent = catalog_config()
    sub = subagent_config(parent, None)
    _, tools, _, _ = instructions_and_tools(sub, subagents=False)
    offered = {tool.name for tool in tools}
    assert {'search_documents', 'list_documents', 'get_document'} <= offered
    assert not {'new_document', 'update_document', 'edit_document'} & offered
    expert = {**catalog_config(), 'expert': True}
    _, tools, _, _ = instructions_and_tools(expert, subagents=True)
    assert not {'new_document', 'update_document', 'edit_document'} & {tool.name for tool in tools}
    _, tools, _, _ = instructions_and_tools(catalog_config(), subagents=True)
    assert {'new_document', 'update_document', 'edit_document'} <= {tool.name for tool in tools}


REMOVED = r'content_editor|is_content_editor|NOT_EDITOR|can_write_document|contentEditor|CONTENT_EDITOR|Content editor'
EDITABLE = r"""['"]editable['"]|\beditable[?]?:|\beditable=|\.editable\b|entry\.editable|editable picks"""


def grep(pattern: str, *paths: str) -> list[str]:
    found = subprocess.run(
        ['git', 'grep', '-n', '-I', '-E', pattern, '--', *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return [line for line in found if '/migrations/' not in line]


def test_the_role_and_the_editable_flag_are_gone():
    paths = ('services/admin-api/src', 'services/admin-ui/app', 'docs/user-guide.md', 'docs/agents.md')
    assert grep(REMOVED, *paths) == []
    assert grep(EDITABLE, *paths) == []
    assert grep(r'\bsave_document\b', *paths) == []


def test_a_code_project_chat_drafts_its_overview_and_lists_its_documents(rev):
    """A run started from a code project writes new_document for it (built for it, named by the first free slug in
    the project from its name and the title)."""
    shop = uuid4()
    with rev.docs.store.connect() as conn:
        conn.execute(
            "INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'shop','Shop')",
            (shop, rev.docs.project),
        )
    make_document(rev.docs, 'shop-overview', 'Another overview')  # not linked to the code project: its slug is taken
    rev.config['access'] = {'resources': [{**entry('code_project', shop, 'Shop'), 'source': 'given'}]}
    reply = call(
        rev,
        'new_document',
        resource={'type': 'code_project', 'name': 'shop'},
        title='Overview',
        content='# Shop overview\n\nPurpose.\n',
    )
    assert reply.endswith(DRAFT_NOTE) and 'built for code project Shop' in reply
    document = created_id(reply)
    with rev.docs.store.connect() as conn:
        draft = conn.execute('SELECT slug FROM ka.documents WHERE id=%s', (document,)).fetchone()
    assert draft['slug'] == 'shop-overview-2'
    listed = json.loads(call(rev, 'list_documents', resource={'type': 'code_project', 'id': str(shop)}))
    assert [item['id'] for item in listed['documents']] == [document]
    again = call(rev, 'new_document', resource={'type': 'code_project', 'id': str(shop)}, title='overview', content='#')
    assert again == DUPLICATE_TITLE.format(title='overview', type='code project', id=document)
    detail = rev.docs.client.get(f'{API}/drafts/{document}', headers=HEADERS).json()
    assert promote(rev.docs, detail).status_code == 200
    assert links(rev, document) == [('code_project', str(shop), 'built_for')]
