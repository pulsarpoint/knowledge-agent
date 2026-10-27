"""search_documents: full-text search over published documents the run may read (given to it, or linked to a server,
service, environment or code project in it), never drafts; ranked title above description above content, with a
short snippet and the document's links to the run's resources."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from drafts_world import docs  # noqa: F401  (fixture)
from knowledge_admin.capabilities import inventory
from knowledge_admin.capabilities.document_tools import check_get_document, search_scope
from knowledge_admin.documents.store import Provenance, create_draft
from knowledge_admin.tools.catalog import CATALOG
from live_documents import link_document, live_document


def access(*given: tuple[str, object]) -> dict:
    return {'resources': [{'type': kind, 'id': str(key), 'name': f'{kind}-{key}'} for kind, key in given]}


def search(config: dict, **arguments):
    spec = CATALOG['search_documents']
    text = asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def ids(found) -> list[str]:
    return [item['document_id'] for item in found['documents']]


@pytest.fixture
def lib(docs, monkeypatch):
    """Two servers (web-1 in the run's reach, db-1 outside it) and documents about rotation: one linked to web-1, one
    to db-1, one linked to nothing (given directly in some runs), one linked to a code project."""
    monkeypatch.setattr(inventory, '_store', docs.store)
    with docs.store.connect() as conn:
        project = docs.project

        def server(name):
            return conn.execute(
                "INSERT INTO ka.servers(project_id,name,hostname,username) VALUES (%s,%s,%s,'ops') RETURNING id",
                (project, name, f'{name}.internal'),
            ).fetchone()['id']

        def doc(slug, title, markdown, **fields):
            return live_document(conn, project, slug, title, markdown, **fields)['id']

        web, db = server('web-1'), server('db-1')
        code = conn.execute(
            """INSERT INTO ka.repositories(project_id,slug,name,remote_url)
            VALUES (%s,'shop-api','shop-api','https://x.test/a.git') RETURNING id""",
            (project,),
        ).fetchone()['id']
        web_doc = doc('web-rotation', 'Web rotation', '# Web rotation\n\nLogs rotate nightly on web-1.')
        db_doc = doc('db-rotation', 'Db rotation', '# Db rotation\n\nLogs rotate weekly on db-1.')
        loose = doc('loose', 'Loose notes', '# Notes\n\nKey rotation happens yearly.')
        code_doc = doc('code-rotation', 'Code notes', '# Code\n\nToken rotation in the api.')
        link_document(conn, project, web_doc, 'server_id', web)
        link_document(conn, project, db_doc, 'server_id', db)
        link_document(conn, project, code_doc, 'code_project_id', code)
    config = {'project_id': str(project), 'access': access(('server', web))}
    return SimpleNamespace(
        docs=docs,
        project=project,
        web=str(web),
        db=str(db),
        code=str(code),
        web_doc=str(web_doc),
        db_doc=str(db_doc),
        loose=str(loose),
        code_doc=str(code_doc),
        config=config,
    )


def test_only_documents_the_run_may_read(lib):
    """Review Focus 2: a document linked only to a resource outside the run is never returned."""
    assert ids(search(lib.config, query='rotation')) == [lib.web_doc]
    given = {**lib.config, 'access': access(('server', lib.web), ('document', lib.loose))}
    assert sorted(ids(search(given, query='rotation'))) == sorted([lib.web_doc, lib.loose])
    coded = {**lib.config, 'access': access(('code_project', lib.code))}
    assert ids(search(coded, query='rotation')) == [lib.code_doc]
    nothing = {**lib.config, 'access': access()}
    assert 'No published document matches' in search(nothing, query='rotation')


def test_a_removed_link_no_longer_makes_a_document_readable(lib):
    with lib.docs.store.connect() as conn:
        actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
        conn.execute(
            'UPDATE ka.document_resources SET removed_at=now(),removed_by=%s WHERE document_id=%s', (actor, lib.web_doc)
        )
    assert isinstance(search(lib.config, query='rotation'), str)


def test_drafts_are_never_returned(lib):
    """Review Focus 2: neither a draft of a readable document nor a brand-new draft (even one given to the run, even
    linked to its server) is searched; the live text is."""
    with lib.docs.store.connect() as conn:
        prov = Provenance('person', None)
        content = {
            'title': 'Web rotation',
            'description': '',
            'purpose': 'explanation',
            'audience': 'both',
            'tags': [],
        }
        create_draft(
            conn,
            lib.project,
            prov,
            {**content, 'markdown': 'Zebra crossing.'},
            origin='manual',
            document_id=lib.web_doc,
        )
        new = create_draft(
            conn,
            lib.project,
            prov,
            {**content, 'title': 'Zebra plan', 'markdown': 'Zebra.'},
            origin='manual',
            slug='zebra',
            links={'server_ids': [lib.web]},
        )
    given = {**lib.config, 'access': access(('server', lib.web), ('document', new))}
    assert 'No published document matches' in search(given, query='zebra')
    assert ids(search(given, query='nightly')) == [lib.web_doc]


def test_title_ranks_above_description_above_content(lib):
    with lib.docs.store.connect() as conn:
        made = [
            live_document(conn, lib.project, slug, title, markdown, description=description)['id']
            for slug, title, description, markdown in (
                ('in-body', 'Alpha', '', '# Alpha\n\nThe failover runs here.'),
                ('in-title', 'Failover', '', '# Failover\n\nSteps.'),
                ('in-description', 'Beta', 'How failover works', '# Beta\n\nSteps.'),
            )
        ]
    config = {**lib.config, 'access': access(*(('document', item) for item in made))}
    assert ids(search(config, query='failover')) == [str(made[1]), str(made[2]), str(made[0])]


def test_a_result_has_its_fields_snippet_and_links_in_the_run(lib):
    with lib.docs.store.connect() as conn:
        conn.execute(
            "UPDATE ka.documents SET description='Rotation of web logs.\nMore.', tags=ARRAY['logs'] WHERE id=%s",
            (lib.web_doc,),
        )
        link_document(conn, lib.project, lib.web_doc, 'server_id', lib.db)  # outside the run: not shown
    [found] = search(lib.config, query='nightly')['documents']
    assert found['document_id'] == lib.web_doc and found['title'] == 'Web rotation'
    assert found['description_short'] == 'Rotation of web logs.' and found['tags'] == ['logs']
    assert found['resources'] == [{'type': 'server', 'id': lib.web, 'name': 'server-' + lib.web}]
    assert '**nightly**' in found['snippet'] and len(found['snippet'].split()) <= 40


def test_a_long_document_gets_a_short_snippet(lib):
    with lib.docs.store.connect() as conn:
        long = live_document(conn, lib.project, 'long', 'Long', 'word ' * 2000 + 'needle ' + 'word ' * 2000)['id']
    [found] = search({**lib.config, 'access': access(('document', long))}, query='needle')['documents']
    assert '**needle**' in found['snippet'] and len(found['snippet'].split()) <= 40


def test_limit(lib):
    with lib.docs.store.connect() as conn:
        made = [
            live_document(conn, lib.project, f'many-{n}', f'Many {n}', '# Many\n\nshared word')['id'] for n in range(30)
        ]
    config = {**lib.config, 'access': access(*(('document', item) for item in made))}
    assert len(search(config, query='shared')['documents']) == 10
    assert len(search(config, query='shared', limit=3)['documents']) == 3
    assert len(search(config, query='shared', limit=25)['documents']) == 25
    with pytest.raises(ValidationError):
        search(config, query='shared', limit=26)


def test_web_search_syntax(lib):
    config = {**lib.config, 'access': access(('server', lib.web), ('document', lib.loose))}
    assert ids(search(config, query='"rotate nightly"')) == [lib.web_doc]
    assert ids(search(config, query='rotation -nightly')) == [lib.loose]


def test_words_are_stemmed(lib):
    """The 'english' configuration: "rotate" finds the documents about rotation."""
    config = {**lib.config, 'access': access(('document', lib.loose))}
    assert ids(search(config, query='rotate')) == [lib.loose]


def test_the_empty_answer_suggests_list_documents(lib):
    answer = search(lib.config, query='nothing-like-this')
    assert answer.startswith('No published document matches') and 'list_documents' in answer


def test_the_scope_is_the_get_document_rule():
    """search_scope gives the SQL the same rule check_get_document applies: a document is readable when given, or
    linked to a server, service, environment or code project in the run (not a deployment); ids that are not uuids
    (a new document's 'new:<uuid>') never match a live document."""
    document, server, service, environment, code, deployment, other = (str(uuid4()) for _ in range(7))
    run = access(
        ('document', document),
        ('document', 'new:' + str(uuid4())),
        ('server', server),
        ('service', service),
        ('environment', environment),
        ('code_project', code),
        ('skill', other),
    )
    scope = search_scope(run)
    assert scope == {
        'document': [document],
        'server': [server],
        'service': [service],
        'environment': [environment],
        'code_project': [code],
    }
    for kind, key in [(k, v[0]) for k, v in scope.items() if k != 'document'] + [('server', other)]:
        assert (check_get_document(run, str(uuid4()), linked=[(kind, key)]) is None) == (key in scope.get(kind, []))
    assert check_get_document(run, str(uuid4()), linked=[('deployment', deployment)]) is not None
