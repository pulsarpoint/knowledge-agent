"""Document drafts in the review count (the environment model's document_drafts) and on the Documents tabs
(GET /api/inventory/{kind}/{id}/documents: a new document's draft listed as a draft, a live document with a draft
waiting flagged; one row per document)."""

from discovery_world import world  # noqa: F401  (fixture)
from live_documents import link_document
from test_repositories import HEADERS
from test_resource_document_list import documents_of


def draft_of(world, found) -> None:
    """A draft of the live nginx runbook (it carries no links of its own)."""
    with world.store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.documents(project_id,slug,revision,is_draft,draft_of,base_version,title,description,
            purpose,audience,tags,markdown,content_hash,origin)
            VALUES (%s,NULL,1,true,%s,1,'nginx runbook','','explanation','both','{}','# d2',
            encode(sha256('# d2'),'hex'),'generated')""",
            (world.project, found['runbook']),
        )


def model(world) -> dict:
    return world.client.get(f'/api/environments/{world.environment}/model', headers=HEADERS).json()


def documents(world, kind, resource_id) -> dict:
    return world.client.get(f'/api/inventory/{kind}/{resource_id}/documents', headers=HEADERS).json()


def test_the_environment_counts_document_drafts_of_its_servers_and_services(world):
    found = documents_of(world)  # a new draft built for web-1
    assert model(world)['document_drafts'] == 1
    draft_of(world, found)  # a draft of nginx's runbook: its document is linked to nginx
    assert model(world)['document_drafts'] == 2
    assert model(world)['drafts'] == 0  # inventory drafts are counted apart


def test_a_draft_elsewhere_is_not_counted(world):
    found = documents_of(world)
    with world.store.connect() as conn:
        draft = conn.execute("SELECT id FROM ka.documents WHERE slug='web-1-draft'").fetchone()['id']
        conn.execute('DELETE FROM ka.document_resources WHERE document_id=%s', (draft,))
        link_document(conn, world.project, draft, 'code_project_id', world.shop, 'built_for')
    assert model(world)['document_drafts'] == 0
    assert found


def test_the_documents_tab_lists_a_new_draft_and_flags_a_document_with_a_draft_waiting(world):
    found = documents_of(world)
    draft_of(world, found)
    server = documents(world, 'server', world.server['id'])['documents']
    by_title = {item['title']: item for item in server}
    assert by_title['A draft']['is_draft'] is True and by_title['A draft']['resource']['name'] == 'web-1'
    assert by_title['nginx runbook']['draft_waiting'] is True and by_title['nginx runbook']['is_draft'] is False
    assert by_title['Alpha shared']['draft_waiting'] is False
    assert [item['title'] for item in server].count('nginx runbook') == 1  # one row per document, not per draft


def new_draft(world, title: str, column: str, resource_id) -> str:
    """A brand-new document's draft built for one resource."""
    with world.store.connect() as conn:
        draft = conn.execute(
            """INSERT INTO ka.documents(project_id,slug,revision,is_draft,title,description,purpose,audience,tags,
            markdown,content_hash,origin)
            VALUES (%s,%s,1,true,%s,'','explanation','both','{}','# n',encode(sha256('# n'),'hex'),'generated')
            RETURNING id""",
            (world.project, title.lower().replace(' ', '-'), title),
        ).fetchone()['id']
        link_document(conn, world.project, draft, column, resource_id, 'built_for')
    return str(draft)


def drafts_to_review(world) -> list[dict]:
    response = world.client.get(f'/api/environments/{world.environment}/document-drafts', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()['drafts']


def test_the_environment_lists_every_counted_draft_with_its_resource(world):
    found = documents_of(world)  # a new draft built for web-1
    draft_of(world, found)  # a draft of nginx's runbook
    service_draft = new_draft(world, 'nginx tuning', 'service_id', world.nginx)
    environment_draft = new_draft(world, 'Production plan', 'environment_id', world.environment)
    listed = drafts_to_review(world)
    assert [(item['title'], item['resource']['type'], item['resource']['name'], item['new']) for item in listed] == [
        ('Production plan', 'environment', 'Production', True),
        ('A draft', 'server', 'web-1', True),
        ('nginx runbook', 'service', 'nginx', False),
        ('nginx tuning', 'service', 'nginx', True),
    ]
    assert listed[0]['id'] == environment_draft and listed[3]['id'] == service_draft
    assert listed[3]['resource']['id'] == str(world.nginx)
    assert model(world)['document_drafts'] == len(listed) == 4


def test_a_draft_tied_by_its_own_pending_link_is_counted_and_listed(world):
    found = documents_of(world)
    with world.store.connect() as conn:  # Shop readme is built for a code project only; its draft links web-1
        draft = conn.execute(
            """INSERT INTO ka.documents(project_id,slug,revision,is_draft,draft_of,base_version,title,description,
            purpose,audience,tags,markdown,content_hash,origin)
            VALUES (%s,NULL,1,true,%s,1,'Shop readme','','explanation','both','{}','# r2',
            encode(sha256('# r2'),'hex'),'generated') RETURNING id""",
            (world.project, found['readme']),
        ).fetchone()['id']
        link_document(conn, world.project, draft, 'server_id', world.server['id'])
    listed = drafts_to_review(world)
    assert ('Shop readme', 'web-1', False) in [
        (item['title'], item['resource']['name'], item['new']) for item in listed
    ]
    assert model(world)['document_drafts'] == 2


def test_no_drafts_to_review_elsewhere(world):
    documents_of(world)
    new_draft(world, 'Shop notes', 'code_project_id', world.shop)
    assert [item['title'] for item in drafts_to_review(world)] == ['A draft']
