"""documents/store.py: one lifecycle for live documents, drafts, the archive and resource links (disposable DB)."""

import threading
from uuid import uuid4

import pytest
from fastapi import HTTPException
from knowledge_admin.documents.store import (
    Provenance,
    build_snapshot,
    create_draft,
    discard_draft,
    draft_outcome,
    promote_draft,
    resource_history,
    resource_links,
    save_links,
    update_draft,
    version,
    versions,
    write_live,
)
from session_rows import a_model, add_ad_hoc_session

EMPTY = {'environments': [], 'code_projects': [], 'services': [], 'servers': [], 'deployments': []}


def content(markdown: str, title: str = 'Runbook') -> dict:
    return {
        'title': title,
        'description': '',
        'purpose': 'explanation',
        'audience': 'both',
        'tags': [],
        'markdown': markdown,
    }


def environment(conn, project, name: str):
    return conn.execute(
        'INSERT INTO ka.environments(project_id,name) VALUES (%s,%s) RETURNING id', (project, name)
    ).fetchone()['id']


def service(conn, project, name: str):
    return conn.execute(
        'INSERT INTO ka.services(project_id,name) VALUES (%s,%s) RETURNING id', (project, name)
    ).fetchone()['id']


def server(conn, project, name: str):
    return conn.execute(
        """INSERT INTO ka.servers(project_id,name,hostname,username,enabled) VALUES (%s,%s,%s,'ops',false)
        RETURNING id""",
        (project, name, name + '.internal'),
    ).fetchone()['id']


def code_project(conn, project, name: str):
    slug = name.lower()
    return conn.execute(
        'INSERT INTO ka.repositories(project_id,slug,name,remote_url) VALUES (%s,%s,%s,%s) RETURNING id',
        (project, slug, name, f'https://git.example/{slug}.git'),
    ).fetchone()['id']


def deployment(conn, project, service_id, server_id):
    return conn.execute(
        """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name)
        VALUES (%s,%s,%s,'production','app') RETURNING id""",
        (project, service_id, server_id),
    ).fetchone()['id']


def new_live(conn, project, markdown='# One', slug='runbook', links=None, prov=None):
    return write_live(
        conn,
        project,
        prov or Provenance('person', None),
        document_id=None,
        slug=slug,
        folder=None,
        content=content(markdown),
        snapshot=EMPTY,
        origin='manual',
        revision=None,
        links=links,
    )


def count(conn, sql: str, args=()) -> int:
    return conn.execute(sql, args).fetchone()['n']


def test_promote_archives_the_live_content_keeps_the_id_and_deletes_every_draft(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        live = write_live(
            conn,
            project,
            prov,
            document_id=None,
            slug='runbook',
            folder=None,
            content=content('# One'),
            snapshot=EMPTY,
            origin='manual',
            revision=None,
        )
        first = create_draft(conn, project, prov, content('# Two'), origin='manual', document_id=live['id'])
        other = create_draft(conn, project, prov, content('# Other'), origin='manual', document_id=live['id'])
        result = promote_draft(conn, project, None, first, revision=1, confirm_stale=False)
        row = conn.execute('SELECT * FROM ka.documents WHERE id=%s', (live['id'],)).fetchone()
        assert (result['document_id'], result['version'], result['deleted_drafts']) == (live['id'], 2, 2)
        assert result['new'] is False and result['revision'] == 2
        assert row['markdown'] == '# Two' and row['from_draft'] and not row['is_draft']
        assert (
            conn.execute(
                'SELECT markdown FROM ka.document_archive WHERE document_id=%s AND version=1', (live['id'],)
            ).fetchone()['markdown']
            == '# One'
        )
        assert count(conn, 'SELECT count(*) AS n FROM ka.documents WHERE id = ANY(%s)', ([first, other],)) == 0
        event = conn.execute(
            "SELECT resource_id,details FROM ka.audit_events WHERE action='document.draft.promoted'"
        ).fetchone()
        assert event['resource_id'] == first
        assert event['details'] == {
            'document_id': str(live['id']),
            'version': 2,
            'stale_confirmed': False,
            'deleted_drafts': 2,
        }


def test_promote_moves_sources_live_to_archive_and_draft_to_live(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        live = new_live(conn, project)
        first = create_draft(conn, project, prov, content('# Two'), origin='manual', document_id=live['id'])
        promote_draft(conn, project, None, first, revision=1, confirm_stale=False)
        archive = conn.execute('SELECT id FROM ka.document_archive WHERE document_id=%s', (live['id'],)).fetchone()
        # the parent source of the promoted draft now belongs to the live row
        assert conn.execute(
            'SELECT kind,source_version FROM ka.document_sources WHERE document_id=%s', (live['id'],)
        ).fetchall() == [{'kind': 'parent', 'source_version': 1}]
        second = create_draft(conn, project, prov, content('# Three'), origin='manual', document_id=live['id'])
        promote_draft(conn, project, None, second, revision=1, confirm_stale=False)
        archive = conn.execute(
            'SELECT id FROM ka.document_archive WHERE document_id=%s AND version=2', (live['id'],)
        ).fetchone()
        assert count(conn, 'SELECT count(*) AS n FROM ka.document_sources WHERE archive_id=%s', (archive['id'],)) == 1
        assert conn.execute(
            'SELECT source_version FROM ka.document_sources WHERE document_id=%s', (live['id'],)
        ).fetchall() == [{'source_version': 2}]


def test_unticking_a_built_for_link_is_refused_and_nothing_changes(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        env = environment(conn, project, 'Prod')
        other = environment(conn, project, 'Staging')
        live = write_live(
            conn,
            project,
            Provenance('person', None),
            document_id=None,
            slug='prod',
            folder=None,
            content=content('# Prod'),
            snapshot=EMPTY,
            origin='manual',
            revision=None,
            links={'environment_ids': [env]},
        )
        with pytest.raises(HTTPException) as error:
            save_links(conn, project, live['id'], {'environment_ids': [other]}, Provenance('person', None))
        assert error.value.status_code == 409
        assert error.value.detail == "This document was built for environment Prod; that link can't be removed."
        assert count(conn, 'SELECT count(*) AS n FROM ka.document_resources WHERE removed_at IS NULL') == 1
        assert count(conn, 'SELECT count(*) AS n FROM ka.document_resources') == 1


def test_built_for_names_a_deployment_by_service_server_and_environment(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        svc, host = service(conn, project, 'Payments'), server(conn, project, 'web-1')
        dep = deployment(conn, project, svc, host)
        live = new_live(conn, project, links={'service_ids': [svc], 'server_ids': [host], 'deployment_ids': [dep]})
        with pytest.raises(HTTPException) as error:
            save_links(conn, project, live['id'], {'deployment_ids': []}, Provenance('person', None))
        assert error.value.detail == (
            "This document was built for deployment Payments / web-1 / production; that link can't be removed."
        )


def test_linked_links_are_added_and_removed_with_history(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'alice@example.com')
        bob = store.actor(conn, 'bob', 'bob@example.com')
        keep, drop, add = (
            service(conn, project, 'Keep'),
            service(conn, project, 'Drop'),
            service(conn, project, 'Add'),
        )
        live = new_live(conn, project)
        assert save_links(conn, project, live['id'], {'service_ids': [keep, drop]}, Provenance('person', alice)) == {
            'added': [str(keep), str(drop)],
            'removed': [],
        }
        kept = conn.execute('SELECT id,created_at FROM ka.document_resources WHERE service_id=%s', (keep,)).fetchone()
        conn.commit()  # a new transaction: now() moves on, so an untouched row shows its old created_at
        result = save_links(conn, project, live['id'], {'service_ids': [keep, add]}, Provenance('person', bob))
        assert result == {'added': [str(add)], 'removed': [str(drop)]}
        assert (
            conn.execute('SELECT id,created_at FROM ka.document_resources WHERE service_id=%s', (keep,)).fetchone()
            == kept
        )
        dropped = conn.execute(
            'SELECT connection,added_how,added_by,removed_at,removed_by FROM ka.document_resources WHERE service_id=%s',
            (drop,),
        ).fetchone()
        assert dropped['connection'] == 'linked' and dropped['added_how'] == 'person' and dropped['added_by'] == alice
        assert dropped['removed_at'] is not None and dropped['removed_by'] == bob
        active = resource_links(conn, live['id'])
        assert [(row['name'], row['type'], row['connection']) for row in active] == [
            ('Keep', 'service', 'linked'),
            ('Add', 'service', 'linked'),
        ]
        assert active[0]['added_by'] == 'alice@example.com'
        history = resource_history(conn, live['id'])
        assert len(history) == 3
        assert {row['name']: row['removed_by'] for row in history}['Drop'] == 'bob@example.com'
        # a removed link can be added again as a new row
        save_links(conn, project, live['id'], {'service_ids': [keep, add, drop]}, Provenance('person', alice))
        assert count(conn, 'SELECT count(*) AS n FROM ka.document_resources WHERE service_id=%s', (drop,)) == 2


def test_a_new_live_document_has_built_for_links_and_version_one(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        env = environment(conn, project, 'Prod')
        live = new_live(conn, project, links={'environment_ids': [env]}, prov=Provenance('discovery', None))
        assert (live['revision'], live['version']) == (1, 1)
        row = conn.execute('SELECT * FROM ka.documents WHERE id=%s', (live['id'],)).fetchone()
        assert row['version_created_at'] is not None and not row['is_draft'] and row['origin'] == 'manual'
        link = conn.execute('SELECT connection,added_how FROM ka.document_resources').fetchone()
        assert link == {'connection': 'built_for', 'added_how': 'discovery'}
        assert conn.execute("SELECT details FROM ka.audit_events WHERE action='document.created'").fetchone()[
            'details'
        ] == {'version': 1}
        with pytest.raises(HTTPException) as error:
            write_live(
                conn,
                project,
                Provenance('person', None),
                document_id=None,
                slug='other',
                folder=None,
                content=content('# X'),
                snapshot=EMPTY,
                origin='manual',
                revision=3,
            )
        assert error.value.status_code == 400


def test_saving_a_live_document_archives_changed_content_only(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        alice = store.actor(conn, 'alice', 'alice@example.com')
        svc = service(conn, project, 'Payments')
        live = new_live(conn, project, prov=Provenance('person', alice))
        prov = Provenance('person', alice)
        saved = write_live(
            conn,
            project,
            prov,
            document_id=live['id'],
            slug='runbook',
            folder='ignored',
            content=content('# Two'),
            snapshot=EMPTY,
            origin='uploaded',
            revision=1,
            links={'service_ids': [svc]},
        )
        assert saved == {'id': live['id'], 'revision': 2, 'version': 2}
        row = conn.execute('SELECT * FROM ka.documents WHERE id=%s', (live['id'],)).fetchone()
        assert row['folder'] is None and row['origin'] == 'uploaded' and row['markdown'] == '# Two'
        archived = conn.execute('SELECT * FROM ka.document_archive WHERE document_id=%s', (live['id'],)).fetchone()
        assert (archived['version'], archived['markdown'], archived['created_by'], archived['archived_by']) == (
            1,
            '# One',
            alice,
            alice,
        )
        assert conn.execute("SELECT details FROM ka.audit_events WHERE action='document.updated'").fetchone()[
            'details'
        ] == {'version': 2, 'links_added': [str(svc)], 'links_removed': []}
        # links only: revision bumps, version stays, nothing archived
        again = write_live(
            conn,
            project,
            prov,
            document_id=live['id'],
            slug='runbook',
            folder=None,
            content=content('# Two'),
            snapshot=EMPTY,
            origin='uploaded',
            revision=2,
            links={'service_ids': []},
        )
        assert again == {'id': live['id'], 'revision': 3, 'version': 2}
        assert count(conn, 'SELECT count(*) AS n FROM ka.document_archive') == 1
        assert [(v['version'], v['live']) for v in versions(conn, live['id'])] == [(2, True), (1, False)]


def test_saving_a_live_document_refuses_stale_revisions_and_drafts(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        live = new_live(conn, project)
        draft = create_draft(conn, project, prov, content('# D'), origin='manual', document_id=live['id'])
        args = {'slug': 'runbook', 'folder': None, 'content': content('# X'), 'snapshot': EMPTY, 'origin': 'manual'}
        with pytest.raises(HTTPException) as error:
            write_live(conn, project, prov, document_id=live['id'], revision=7, **args)
        assert error.value.status_code == 409
        assert error.value.detail == (
            'This document changed. Reload it before saving; your text has not been overwritten.'
        )
        for missing in (draft, uuid4()):
            with pytest.raises(HTTPException) as error:
                write_live(conn, project, prov, document_id=missing, revision=1, **args)
            assert (error.value.status_code, error.value.detail) == (404, 'Document not found')


def test_a_run_draft_of_a_new_document_has_built_for_links_and_sets_last_draft(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'alice', 'alice@example.com')
        session = add_ad_hoc_session(conn, project, actor, a_model(conn, actor))
        env = environment(conn, project, 'Prod')
        prov = Provenance('agent_run', actor, session)
        draft = create_draft(
            conn,
            project,
            prov,
            content('# New'),
            origin='generated',
            slug='payments',
            folder='payments',
            links={'environment_ids': [env]},
        )
        row = conn.execute('SELECT * FROM ka.documents WHERE id=%s', (draft,)).fetchone()
        assert row['is_draft'] and row['draft_of'] is None and (row['slug'], row['folder']) == ('payments', 'payments')
        assert row['origin'] == 'generated' and row['session_id'] == session and row['created_by'] == actor
        link = conn.execute(
            'SELECT connection,added_how,added_by,session_id,environment_id FROM ka.document_resources'
        ).fetchone()
        assert link == {
            'connection': 'built_for',
            'added_how': 'agent_run',
            'added_by': actor,
            'session_id': session,
            'environment_id': env,
        }
        assert (
            conn.execute('SELECT last_draft_id FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()[
                'last_draft_id'
            ]
            == draft
        )
        event = conn.execute(
            "SELECT resource_type,details FROM ka.audit_events WHERE action='document.draft.created'"
        ).fetchone()
        assert event['resource_type'] == 'document_draft'
        assert event['details'] == {'document_id': None, 'origin': 'generated', 'session_id': str(session)}


def test_a_folder_and_slug_naming_a_live_document_make_a_draft_of_it(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        live = new_live(conn, project)
        env = environment(conn, project, 'Prod')
        draft = create_draft(
            conn,
            project,
            prov,
            content('# Again'),
            origin='manual',
            slug='runbook',
            folder=None,
            links={'environment_ids': [env]},
        )
        row = conn.execute('SELECT * FROM ka.documents WHERE id=%s', (draft,)).fetchone()
        assert (row['draft_of'], row['base_version'], row['slug'], row['folder']) == (live['id'], 1, None, None)
        assert count(conn, 'SELECT count(*) AS n FROM ka.document_resources') == 0
        assert conn.execute(
            'SELECT kind,source_document_id,source_title,source_version FROM ka.document_sources WHERE document_id=%s',
            (draft,),
        ).fetchone() == {
            'kind': 'parent',
            'source_document_id': live['id'],
            'source_title': 'Runbook',
            'source_version': 1,
        }


def test_promoting_a_new_draft_keeps_its_id_and_links(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'alice', 'alice@example.com')
        repo = code_project(conn, project, 'Shop')
        draft = create_draft(
            conn,
            project,
            Provenance('person', actor),
            content('# New'),
            origin='manual',
            slug='shop',
            folder=None,
            links={'code_project_ids': [repo]},
        )
        links = conn.execute('SELECT * FROM ka.document_resources').fetchall()
        result = promote_draft(conn, project, actor, draft, revision=1, confirm_stale=False)
        assert result == {'document_id': draft, 'version': 1, 'revision': 2, 'deleted_drafts': 0, 'new': True}
        row = conn.execute('SELECT * FROM ka.documents WHERE id=%s', (draft,)).fetchone()
        assert not row['is_draft'] and row['version'] == 1 and row['from_draft']
        assert row['version_created_by'] == actor and row['version_created_at'] is not None
        assert row['source_snapshot']['code_projects'] == [{'id': str(repo), 'name': 'Shop'}]
        assert conn.execute('SELECT * FROM ka.document_resources').fetchall() == links
        assert conn.execute('SELECT id FROM ka.document_details').fetchall() == [{'id': draft}]


def test_a_stale_draft_needs_confirmation(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        live = new_live(conn, project)
        draft = create_draft(conn, project, prov, content('# Draft'), origin='manual', document_id=live['id'])
        write_live(
            conn,
            project,
            prov,
            document_id=live['id'],
            slug='runbook',
            folder=None,
            content=content('# Two'),
            snapshot=EMPTY,
            origin='manual',
            revision=1,
        )
        with pytest.raises(HTTPException) as error:
            promote_draft(conn, project, None, draft, revision=1, confirm_stale=False)
        assert error.value.status_code == 409
        assert error.value.detail == {
            'message': "This draft was based on version 1; the live version is now 2. Promoting replaces version 2's text.",
            'code': 'STALE_BASE',
        }
        result = promote_draft(conn, project, None, draft, revision=1, confirm_stale=True)
        assert result['version'] == 3
        assert [v['version'] for v in versions(conn, live['id'])] == [3, 2, 1]
        assert conn.execute(
            "SELECT details->'stale_confirmed' AS s FROM ka.audit_events WHERE action='document.draft.promoted'"
        ).fetchone()['s']


def test_promote_checks_the_draft_revision(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        live = new_live(conn, project)
        draft = create_draft(
            conn, project, Provenance('person', None), content('# D'), origin='manual', document_id=live['id']
        )
        with pytest.raises(HTTPException) as error:
            promote_draft(conn, project, None, draft, revision=5, confirm_stale=False)
        assert (error.value.status_code, error.value.detail) == (
            409,
            'This draft changed. Reload it before saving; your text has not been overwritten.',
        )


def test_two_concurrent_promotes_of_one_document_archive_each_version_once(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        live = new_live(conn, project)
        drafts = [
            create_draft(conn, project, prov, content(f'# {n}'), origin='manual', document_id=live['id'])
            for n in ('A', 'B')
        ]
    barrier = threading.Barrier(2)
    outcomes: dict = {}

    def promote(draft_id):
        try:
            with store.connect() as conn:
                barrier.wait(timeout=10)
                outcomes[draft_id] = promote_draft(conn, project, None, draft_id, revision=1, confirm_stale=False)
        except HTTPException as error:
            outcomes[draft_id] = error

    threads = [threading.Thread(target=promote, args=(draft_id,)) for draft_id in drafts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    results = [outcomes[draft_id] for draft_id in drafts]
    won = [result for result in results if isinstance(result, dict)]
    lost = [result for result in results if isinstance(result, HTTPException)]
    assert len(won) == 1 and len(lost) == 1
    assert lost[0].status_code in (404, 409)
    with store.connect() as conn:
        assert conn.execute(
            'SELECT version,count(*) AS n FROM ka.document_archive WHERE document_id=%s GROUP BY version',
            (live['id'],),
        ).fetchall() == [{'version': 1, 'n': 1}]
        assert conn.execute('SELECT version FROM ka.documents WHERE id=%s', (live['id'],)).fetchone()['version'] == 2
        assert count(conn, 'SELECT count(*) AS n FROM ka.documents WHERE is_draft') == 0


def test_update_draft_bumps_revision_and_keeps_edited(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        live = new_live(conn, project)
        draft = create_draft(
            conn, project, Provenance('person', None), content('# D'), origin='generated', document_id=live['id']
        )
        assert update_draft(conn, draft, None, content('# E'), revision=1, edited=True) == 2
        assert update_draft(conn, draft, None, content('# F'), revision=None, edited=False) == 3
        row = conn.execute('SELECT markdown,edited,revision FROM ka.documents WHERE id=%s', (draft,)).fetchone()
        assert row == {'markdown': '# F', 'edited': True, 'revision': 3}
        with pytest.raises(HTTPException) as error:
            update_draft(conn, draft, None, content('# G'), revision=1, edited=False)
        assert error.value.status_code == 409


def test_update_and_discard_of_a_deleted_draft_are_404(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        live = new_live(conn, project)
        promoted = create_draft(conn, project, prov, content('# P'), origin='manual', document_id=live['id'])
        gone = create_draft(conn, project, prov, content('# G'), origin='manual', document_id=live['id'])
        promote_draft(conn, project, None, promoted, revision=1, confirm_stale=False)
        for draft in (promoted, gone, uuid4()):
            with pytest.raises(HTTPException) as error:
                update_draft(conn, draft, None, content('# X'), revision=1, edited=True)
            assert (error.value.status_code, error.value.detail) == (404, 'Draft not found.')
            with pytest.raises(HTTPException) as error:
                discard_draft(conn, project, None, draft, revision=1)
            assert error.value.status_code == 404
            with pytest.raises(HTTPException) as error:
                promote_draft(conn, project, None, draft, revision=1, confirm_stale=False)
            assert error.value.status_code == 404


def test_discard_deletes_the_draft_and_a_new_drafts_links(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        env = environment(conn, project, 'Prod')
        draft = create_draft(
            conn, project, prov, content('# N'), origin='manual', slug='n', links={'environment_ids': [env]}
        )
        with pytest.raises(HTTPException) as error:
            discard_draft(conn, project, None, draft, revision=2)
        assert error.value.status_code == 409
        discard_draft(conn, project, None, draft, revision=1)
        assert count(conn, 'SELECT count(*) AS n FROM ka.documents') == 0
        assert count(conn, 'SELECT count(*) AS n FROM ka.document_resources') == 0
        event = conn.execute(
            "SELECT resource_id,details FROM ka.audit_events WHERE action='document.draft.discarded'"
        ).fetchone()
        assert event == {'resource_id': draft, 'details': {'document_id': None}}


def test_draft_outcome_open_promoted_discarded_unknown(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        live = new_live(conn, project)
        promoted = create_draft(conn, project, prov, content('# P'), origin='manual', document_id=live['id'])
        assert draft_outcome(conn, promoted) == {'id': promoted, 'document_id': live['id'], 'status': 'open'}
        promote_draft(conn, project, None, promoted, revision=1, confirm_stale=False)
        assert draft_outcome(conn, promoted) == {'id': promoted, 'document_id': live['id'], 'status': 'promoted'}
        discarded = create_draft(conn, project, prov, content('# D'), origin='manual', document_id=live['id'])
        discard_draft(conn, project, None, discarded, revision=1)
        assert draft_outcome(conn, discarded) == {'id': discarded, 'document_id': live['id'], 'status': 'discarded'}
        brand_new = create_draft(conn, project, prov, content('# B'), origin='manual', slug='brand-new')
        assert draft_outcome(conn, brand_new) == {'id': brand_new, 'document_id': brand_new, 'status': 'open'}
        promote_draft(conn, project, None, brand_new, revision=1, confirm_stale=False)
        assert draft_outcome(conn, brand_new)['status'] == 'promoted'
        assert draft_outcome(conn, uuid4()) is None


def test_versions_are_newest_first_and_version_reads_by_number(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        live = new_live(conn, project)
        for revision, text in ((1, '# Two'), (2, '# Three')):
            write_live(
                conn,
                project,
                prov,
                document_id=live['id'],
                slug='runbook',
                folder=None,
                content=content(text),
                snapshot=EMPTY,
                origin='manual',
                revision=revision,
            )
        listed = versions(conn, live['id'])
        assert [(v['version'], v['live'], v['from_draft']) for v in listed] == [
            (3, True, False),
            (2, False, False),
            (1, False, False),
        ]
        assert set(listed[0]) == {'version', 'title', 'origin', 'created_at', 'from_draft', 'live'}
        assert version(conn, live['id'], 2)['markdown'] == '# Two'
        assert version(conn, live['id'], 3)['live'] is True
        assert version(conn, live['id'], 9) is None


def test_build_snapshot_checks_resources_and_carries_commits(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        repo = code_project(conn, project, 'Shop')
        svc, host = service(conn, project, 'Payments'), server(conn, project, 'web-1')
        dep = deployment(conn, project, svc, host)
        commit = 'a' * 40
        snapshot = build_snapshot(conn, project, {'code_project_ids': [repo]}, {str(repo): commit}, None)
        assert snapshot['code_projects'] == [{'id': str(repo), 'name': 'Shop', 'git_commit': commit}]
        carried = build_snapshot(conn, project, {'code_project_ids': [repo]}, None, snapshot)
        assert carried['code_projects'][0]['git_commit'] == commit
        for ids, commits, message in (
            ({'service_ids': [uuid4()]}, None, 'A selected resource is no longer available'),
            ({'deployment_ids': [dep], 'service_ids': [svc]}, None, 'Select the service and server'),
            ({}, {str(repo): commit}, 'Source commits must belong'),
        ):
            with pytest.raises(HTTPException) as error:
                build_snapshot(conn, project, ids, commits, None)
            assert error.value.status_code == 400 and error.value.detail.startswith(message)
