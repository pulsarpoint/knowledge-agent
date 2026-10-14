"""0104: live documents and drafts in ka.documents, ka.document_archive, ka.document_resources."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from session_rows import a_model

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0104_document_model.up.sql', '0104_document_model.down.sql'
ARRAYS = 'code_project_ids,service_ids,server_ids,deployment_ids,environment_ids'
# What 0104 changes: the snapshot a rollback must restore.
TABLES = [
    'documents',
    'document_versions',
    'document_drafts',
    'document_sources',
    'document_environments',
    'document_services',
    'document_servers',
    'document_code_projects',
    'document_deployments',
    'document_archive',
    'document_resources',
    'document_links',
    'inventory_documents',
    'agent_sessions',
    'discovery_proposals',
]
VIEWS = ['document_details', 'live_documents', 'document_drafts_view']
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def before(conn):
    """Roll 0104 back (0106 first: discovery runs) so old-shape rows can be written."""
    conn.commit()
    run(conn, '0106_resource_runs.down.sql')
    run(conn, DOWN)


def old_document(conn, project, slug, versions, folder=None, links=None):
    """A document in the 0103 shape with `versions` markdown bodies (last one current); returns (id, [version ids]).

    The document row goes first (versions reference it); current_version_id is a deferred foreign key, so it may name
    a version inserted later in the same transaction."""
    doc, ids = uuid4(), [uuid4() for _ in versions]
    conn.execute(
        'INSERT INTO ka.documents(id,project_id,slug,folder,current_version_id,revision) VALUES (%s,%s,%s,%s,%s,%s)',
        (doc, project, slug, folder, ids[-1], len(ids)),
    )
    for number, (version_id, body) in enumerate(zip(ids, versions, strict=True), start=1):
        conn.execute(
            """INSERT INTO ka.document_versions(id,document_id,project_id,version,title,description,purpose,audience,
            tags,markdown,content_hash,source_snapshot,origin,created_at) VALUES (%s,%s,%s,%s,%s,'','explanation',
            'both','{}',%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),'{"code_projects": []}','manual',%s)""",
            (version_id, doc, project, number, f'{slug} v{number}', body, body, EPOCH + timedelta(days=number)),
        )
    for table, column, value in links or ():
        conn.execute(
            f'INSERT INTO ka.{table}(document_id,project_id,{column}) VALUES (%s,%s,%s)', (doc, project, value)
        )
    return doc, ids


def old_draft(conn, project, **columns):
    """A row of the 0103 ka.document_drafts; returns its id."""
    values = {'project_id': project, 'title': 'Draft', 'markdown': '# Draft', 'origin': 'manual', **columns}
    if values.get('status', 'open') != 'open':
        values.setdefault('decided_at', EPOCH)
    if isinstance(values.get('resource_ids'), dict):
        values['resource_ids'] = Jsonb(values['resource_ids'])
    names, marks = ','.join(values), ','.join(['%s'] * len(values))
    return conn.execute(
        f'INSERT INTO ka.document_drafts({names}) VALUES ({marks}) RETURNING id', tuple(values.values())
    ).fetchone()['id']


def actor(conn, store):
    return store.actor(conn, 'migration-tester', 'migration-tester@example.test')


def session(conn, project, person, purpose='documents', **columns):
    """An agent run: a Generate documents run by default."""
    if purpose == 'documents':
        columns = {'document_name': 'Doc', 'folder': 'docs', **columns}
    values = {
        'id': uuid4(),
        'project_id': project,
        'actor_id': person,
        'model_id': a_model(conn, person),
        'title': 'Run',
        'prompt_snapshot': Jsonb({}),
        'context_snapshot': Jsonb({}),
        'agent_config': Jsonb({}),
        'purpose': purpose,
        **columns,
    }
    names, marks = ','.join(values), ','.join(['%s'] * len(values))
    conn.execute(f'INSERT INTO ka.agent_sessions({names}) VALUES ({marks})', tuple(values.values()))
    return values['id']


def resources(conn, project):
    """One environment, service, server, code project and deployment."""
    ids = {
        'environment': conn.execute(
            "INSERT INTO ka.environments(project_id,name) VALUES (%s,'production') RETURNING id", (project,)
        ).fetchone()['id'],
        'service': conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'api') RETURNING id", (project,)
        ).fetchone()['id'],
        'server': conn.execute(
            "INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'prod-1',%s,false) RETURNING id",
            (project, f'{uuid4().hex[:8]}.internal'),
        ).fetchone()['id'],
        'code_project': conn.execute(
            """INSERT INTO ka.repositories(project_id,slug,name,remote_url,requested_ref,checkout_path)
            VALUES (%s,%s,'api-code','https://example.test/api.git','main',%s) RETURNING id""",
            (project, f'api-code-{uuid4().hex[:6]}', f'r-{uuid4().hex[:8]}'),
        ).fetchone()['id'],
    }
    ids['deployment'] = conn.execute(
        """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id)
        VALUES (%s,%s,%s,'production',%s) RETURNING id""",
        (project, ids['service'], ids['server'], ids['environment']),
    ).fetchone()['id']
    return ids


def all_links(ids):
    return [
        ('document_environments', 'environment_id', ids['environment']),
        ('document_services', 'service_id', ids['service']),
        ('document_servers', 'server_id', ids['server']),
        ('document_code_projects', 'code_project_id', ids['code_project']),
        ('document_deployments', 'deployment_id', ids['deployment']),
    ]


def row(conn, query, *params):
    return conn.execute(query, params).fetchone()


def rows(conn, query, *params):
    return conn.execute(query, params).fetchall()


def schema(conn):
    """Columns (in order), constraints, indexes, triggers and views of what 0104 changes."""
    return {
        'columns': rows(
            conn,
            """SELECT table_name, array_agg(concat_ws(' ', column_name, data_type, domain_name, is_nullable,
                column_default, is_generated, generation_expression) ORDER BY ordinal_position) AS columns
            FROM information_schema.columns WHERE table_schema = 'ka' AND table_name = ANY(%s)
            GROUP BY table_name ORDER BY table_name""",
            TABLES + VIEWS,
        ),
        'constraints': rows(
            conn,
            """SELECT c.conrelid::regclass::text AS on_table, c.conname, pg_get_constraintdef(c.oid) AS definition
            FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'ka' AND t.relname = ANY(%s) ORDER BY 1, 2""",
            TABLES,
        ),
        'indexes': rows(
            conn,
            """SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname = 'ka' AND tablename = ANY(%s) ORDER BY 1, 2""",
            TABLES,
        ),
        'triggers': rows(
            conn,
            """SELECT t.tgrelid::regclass::text AS on_table, t.tgname, pg_get_triggerdef(t.oid) AS definition
            FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE NOT t.tgisinternal AND n.nspname = 'ka' AND c.relname = ANY(%s) ORDER BY 1, 2""",
            TABLES,
        ),
        'views': rows(
            conn,
            "SELECT viewname, definition FROM pg_views WHERE schemaname = 'ka' AND viewname = ANY(%s) ORDER BY 1",
            VIEWS,
        ),
    }


def test_a_document_keeps_its_id_slug_folder_and_current_content(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        doc, (first, second) = old_document(conn, project, 'runbook', ['# One', '# Two'], folder='ops')
        updated = row(conn, 'SELECT updated_at FROM ka.documents WHERE id=%s', doc)['updated_at']
        conn.commit()
        run(conn, UP)
        live = row(conn, 'SELECT * FROM ka.documents WHERE id=%s', doc)
        assert (live['is_draft'], live['version'], live['markdown'], live['slug'], live['folder']) == (
            False,
            2,
            '# Two',
            'runbook',
            'ops',
        )
        assert (live['title'], live['origin'], live['from_draft']) == ('runbook v2', 'manual', False)
        assert live['version_created_at'] == EPOCH + timedelta(days=2)
        assert live['updated_at'] == updated
        archive = rows(
            conn, 'SELECT id,version,markdown,archived_at FROM ka.document_archive WHERE document_id=%s', doc
        )
        assert archive == [{'id': first, 'version': 1, 'markdown': '# One', 'archived_at': EPOCH + timedelta(days=2)}]
        assert row(conn, 'SELECT count(*) AS n FROM ka.document_archive WHERE id=%s', second)['n'] == 0
        assert row(conn, 'SELECT count(*) AS n FROM ka.live_documents WHERE id=%s', doc)['n'] == 1


def test_links_become_migrated_document_resources(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        ids = resources(conn, project)
        doc, _ = old_document(conn, project, 'runbook', ['# One'], links=all_links(ids))
        old = row(conn, f'SELECT {ARRAYS} FROM ka.document_details WHERE id=%s', doc)
        conn.commit()
        run(conn, UP)
        links = rows(
            conn,
            """SELECT resource_type,connection,added_how,removed_at FROM ka.document_resources
            WHERE document_id=%s ORDER BY resource_type""",
            doc,
        )
        assert links == [
            {'resource_type': kind, 'connection': 'linked', 'added_how': 'migrated', 'removed_at': None}
            for kind in sorted(ids)
        ]
        assert row(conn, f'SELECT {ARRAYS} FROM ka.document_details WHERE id=%s', doc) == old
        assert all(old[key] for key in old)


def test_open_drafts_become_draft_rows_and_decided_drafts_are_dropped(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        doc, (first, second) = old_document(conn, project, 'runbook', ['# One', '# Two'])
        person = actor(conn, store)
        run_id = session(conn, project, person)
        opened = old_draft(
            conn,
            project,
            document_id=doc,
            base_version_id=first,
            origin='agent',
            session_id=run_id,
            markdown='# Open',
            edited=True,
            created_by=person,
        )
        promoted = old_draft(
            conn, project, document_id=doc, base_version_id=first, status='promoted', promoted_version_id=second
        )
        discarded = old_draft(conn, project, document_id=doc, base_version_id=first, status='discarded')
        conn.commit()
        run(conn, UP)
        drafts = rows(
            conn,
            """SELECT id,draft_of,base_version,origin,session_id,markdown,edited,slug,folder,version,created_by
            FROM ka.documents WHERE is_draft""",
        )
        assert drafts == [
            {
                'id': opened,
                'draft_of': doc,
                'base_version': 1,
                'origin': 'generated',
                'session_id': run_id,
                'markdown': '# Open',
                'edited': True,
                'slug': None,
                'folder': None,
                'version': None,
                'created_by': person,
            }
        ]
        gone = rows(conn, 'SELECT id FROM ka.documents WHERE id = ANY(%s)', [promoted, discarded])
        assert gone == []
        assert row(conn, 'SELECT from_draft FROM ka.documents WHERE id=%s', doc)['from_draft'] is True
        view = row(conn, 'SELECT document_id,slug,base_version,live_version,stale FROM ka.document_drafts_view')
        assert view == {'document_id': doc, 'slug': 'runbook', 'base_version': 1, 'live_version': 2, 'stale': True}
        assert row(conn, 'SELECT draft_count FROM ka.document_details WHERE id=%s', doc)['draft_count'] == 1


def test_a_new_document_draft_keeps_its_name_and_gets_built_for_links(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        ids = resources(conn, project)
        person = actor(conn, store)
        run_id = session(conn, project, person)
        draft = old_draft(
            conn,
            project,
            slug='disk-layout',
            folder='servers/web-1',
            session_id=run_id,
            origin='agent',
            created_by=person,
            resource_ids={'environment_ids': [str(ids['environment']), str(uuid4())], 'service_ids': []},
        )
        conn.commit()
        run(conn, UP)
        new = row(conn, 'SELECT is_draft,draft_of,slug,folder,base_version FROM ka.documents WHERE id=%s', draft)
        assert new == {
            'is_draft': True,
            'draft_of': None,
            'slug': 'disk-layout',
            'folder': 'servers/web-1',
            'base_version': None,
        }
        links = rows(
            conn,
            """SELECT environment_id,connection,added_how,added_by,session_id,removed_at FROM ka.document_resources
            WHERE document_id=%s""",
            draft,
        )
        assert links == [
            {
                'environment_id': ids['environment'],
                'connection': 'built_for',
                'added_how': 'agent_run',
                'added_by': person,
                'session_id': run_id,
                'removed_at': None,
            }
        ]
        view = row(conn, 'SELECT environment_ids,slug,stale FROM ka.document_drafts_view WHERE id=%s', draft)
        assert view == {'environment_ids': [ids['environment']], 'slug': 'disk-layout', 'stale': False}
        assert row(conn, 'SELECT count(*) AS n FROM ka.document_details WHERE id=%s', draft)['n'] == 0


def test_a_new_document_draft_named_like_a_live_document_becomes_its_draft(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        doc, _ = old_document(conn, project, 'runbook', ['# One', '# Two'], folder='ops')
        draft = old_draft(conn, project, slug='runbook', folder='ops')
        other = old_draft(conn, project, slug='runbook')  # another folder: its own name
        conn.commit()
        run(conn, UP)
        moved = row(conn, 'SELECT draft_of,slug,folder,base_version FROM ka.documents WHERE id=%s', draft)
        assert moved == {'draft_of': doc, 'slug': None, 'folder': None, 'base_version': 2}
        kept = row(conn, 'SELECT draft_of,slug,folder FROM ka.documents WHERE id=%s', other)
        assert kept == {'draft_of': None, 'slug': 'runbook', 'folder': None}


def test_two_new_document_drafts_with_one_name_get_numbered(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        older = old_draft(conn, project, slug='disk-layout', folder='servers', created_at=EPOCH)
        newer = old_draft(conn, project, slug='disk-layout', folder='servers', created_at=EPOCH + timedelta(hours=1))
        newest = old_draft(conn, project, slug='disk-layout', folder='servers', created_at=EPOCH + timedelta(hours=2))
        conn.commit()
        run(conn, UP)
        slugs = {
            item['id']: item['slug']
            for item in rows(conn, 'SELECT id,slug FROM ka.documents WHERE id = ANY(%s)', [older, newer, newest])
        }
        assert slugs == {older: 'disk-layout', newer: 'disk-layout-2', newest: 'disk-layout-3'}


def test_sources_follow_their_owner(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        doc, (first, second) = old_document(conn, project, 'runbook', ['# One', '# Two'])
        source, (source_version,) = old_document(conn, project, 'overview', ['# Overview'])
        opened = old_draft(conn, project, document_id=doc, base_version_id=second)
        decided = old_draft(conn, project, document_id=doc, base_version_id=second, status='discarded')
        add = """INSERT INTO ka.document_sources(project_id,draft_id,version_id,kind,source_document_id,
            source_version_id,source_title,source_version) VALUES (%s,%s,%s,'read',%s,%s,'Overview',1) RETURNING id"""
        owned = {
            name: row(conn, add, project, draft, version, source, source_version)['id']
            for name, draft, version in (
                ('current', None, second),
                ('older', None, first),
                ('open', opened, None),
                ('decided', decided, None),
            )
        }
        conn.commit()
        run(conn, UP)
        found = {
            item['id']: (item['document_id'], item['archive_id'], item['source_document_id'], item['source_version'])
            for item in rows(conn, 'SELECT * FROM ka.document_sources')
        }
        assert found == {
            owned['current']: (doc, None, source, 1),
            owned['older']: (None, first, source, 1),
            owned['open']: (opened, None, source, 1),
        }


def test_sessions_remember_their_last_draft(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        doc, (version,) = old_document(conn, project, 'runbook', ['# One'])
        person = actor(conn, store)
        busy, idle = session(conn, project, person), session(conn, project, person)
        old_draft(conn, project, document_id=doc, base_version_id=version, session_id=busy, created_at=EPOCH)
        newer = old_draft(
            conn,
            project,
            document_id=doc,
            base_version_id=version,
            session_id=busy,
            status='discarded',
            created_at=EPOCH + timedelta(hours=1),
        )
        touched = row(conn, 'SELECT updated_at FROM ka.agent_sessions WHERE id=%s', busy)['updated_at']
        conn.commit()
        run(conn, UP)
        found = {
            item['id']: (item['last_draft_id'], item['updated_at'])
            for item in rows(conn, 'SELECT id,last_draft_id,updated_at FROM ka.agent_sessions')
        }
        assert found[busy] == (newer, touched)
        assert found[idle][0] is None


def test_drafts_cannot_be_used_as_documents(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        ids = resources(conn, project)
        doc, (version,) = old_document(conn, project, 'runbook', ['# One'])
        draft = old_draft(conn, project, document_id=doc, base_version_id=version)
        new = old_draft(conn, project, slug='disk-layout')
        person = actor(conn, store)
        discovery = session(conn, project, person, purpose='discovery', server_id=ids['server'])
        conn.commit()
        run(conn, UP)
        refused = [
            ('INSERT INTO ka.document_links(project_id,from_document_id,to_document_id) VALUES (%s,%s,%s)', doc, draft),
            ('INSERT INTO ka.document_links(project_id,from_document_id,to_document_id) VALUES (%s,%s,%s)', new, doc),
            (
                'INSERT INTO ka.inventory_documents(project_id,environment_id,document_id) VALUES (%s,%s,%s)',
                ids['environment'],
                draft,
            ),
            (
                """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload,document_id)
                VALUES (%s,%s,%s,1,'document','{}',%s)""",
                discovery,
                ids['server'],
                draft,
            ),
            (
                """INSERT INTO ka.document_resources(project_id,document_id,environment_id,connection,added_how)
                VALUES (%s,%s,%s,'linked','person')""",
                draft,
                ids['environment'],
            ),
            (
                """INSERT INTO ka.documents(project_id,is_draft,draft_of,title,description,purpose,audience,markdown,
                content_hash,origin) VALUES (%s,true,%s,'Draft','','explanation','both','# D',
                encode(sha256(convert_to('# D','UTF8')),'hex'),'manual')""",
                new,
            ),
        ]
        for query, *params in refused:
            with pytest.raises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute(query, (project, *params))
        with pytest.raises(psycopg.errors.RaiseException), conn.transaction():
            session(conn, project, person, purpose='revision', target_document_id=draft)
        with pytest.raises(psycopg.errors.RaiseException), conn.transaction():
            conn.execute('UPDATE ka.documents SET is_draft=true WHERE id=%s', (doc,))
        # A brand-new draft owns its links; a live document is fine everywhere.
        conn.execute(
            """INSERT INTO ka.document_resources(project_id,document_id,environment_id,connection,added_how)
            VALUES (%s,%s,%s,'built_for','person')""",
            (project, new, ids['environment']),
        )
        conn.execute(
            'INSERT INTO ka.inventory_documents(project_id,environment_id,document_id) VALUES (%s,%s,%s)',
            (project, ids['environment'], doc),
        )
        session(conn, project, person, purpose='revision', target_document_id=doc)


def test_drafts_keep_at_most_30_tags_and_live_documents_have_no_limit(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        doc, _ = old_document(conn, project, 'runbook', ['# One'])
        conn.commit()
        run(conn, UP)
        add = """INSERT INTO ka.documents(project_id,slug,is_draft,version,version_created_at,title,description,purpose,
            audience,tags,markdown,content_hash,origin) VALUES (%s,%s,%s,%s,%s,'Doc','','explanation','both',%s,'# D',
            encode(sha256(convert_to('# D','UTF8')),'hex'),'manual')"""
        tags = [f'tag-{number}' for number in range(31)]
        with pytest.raises(psycopg.errors.CheckViolation, match='documents_draft_tags_limit'), conn.transaction():
            conn.execute(add, (project, 'new-draft', True, None, None, tags))
        conn.execute(add, (project, 'new-draft', True, None, None, tags[:30]))
        conn.execute(add, (project, 'live', False, 1, EPOCH, tags))
        with pytest.raises(psycopg.errors.CheckViolation, match='documents_draft_tags_limit'), conn.transaction():
            conn.execute(
                """INSERT INTO ka.documents(project_id,is_draft,draft_of,title,description,purpose,audience,tags,
                markdown,content_hash,origin) VALUES (%s,true,%s,'Doc','','explanation','both',%s,'# D',
                encode(sha256(convert_to('# D','UTF8')),'hex'),'manual')""",
                (project, doc, tags),
            )


def test_a_built_for_link_cannot_be_removed(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        ids = resources(conn, project)
        draft = old_draft(
            conn, project, slug='disk-layout', resource_ids={'environment_ids': [str(ids['environment'])]}
        )
        doc, _ = old_document(conn, project, 'runbook', ['# One'])
        person = actor(conn, store)
        conn.commit()
        run(conn, UP)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                'UPDATE ka.document_resources SET removed_at=now(),removed_by=%s WHERE document_id=%s', (person, draft)
            )
        add = """INSERT INTO ka.document_resources(project_id,document_id,environment_id,connection,added_how,added_by)
            VALUES (%s,%s,%s,'linked','person',%s) RETURNING id"""
        first = row(conn, add, project, doc, ids['environment'], person)['id']
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(add, (project, doc, ids['environment'], person))
        conn.execute('UPDATE ka.document_resources SET removed_at=now(),removed_by=%s WHERE id=%s', (person, first))
        conn.execute(add, (project, doc, ids['environment'], person))
        history = rows(
            conn,
            'SELECT removed_at IS NULL AS active FROM ka.document_resources WHERE document_id=%s ORDER BY active',
            doc,
        )
        assert history == [{'active': False}, {'active': True}]


def test_rollback_restores_the_old_shape(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        snapshot = schema(conn)
        ids = resources(conn, project)
        doc, (first, _) = old_document(conn, project, 'runbook', ['# One', '# Two'], folder='ops', links=all_links(ids))
        source, (source_version,) = old_document(conn, project, 'overview', ['# Overview'])
        draft = old_draft(conn, project, document_id=doc, base_version_id=first, origin='agent', markdown='# Open')
        new = old_draft(
            conn,
            project,
            slug='disk-layout',
            folder='servers',
            resource_ids={key: [] for key in ARRAYS.split(',')} | {'server_ids': [str(ids['server'])]},
        )
        conn.execute(
            """INSERT INTO ka.document_sources(project_id,draft_id,kind,source_document_id,source_version_id,
            source_title,source_version) VALUES (%s,%s,'read',%s,%s,'Overview',1)""",
            (project, draft, source, source_version),
        )
        details = rows(conn, 'SELECT * FROM ka.document_details ORDER BY slug')
        conn.commit()
        run(conn, UP)
        conn.commit()
        run(conn, DOWN)
        versions = rows(
            conn, 'SELECT id,version,markdown FROM ka.document_versions WHERE document_id=%s ORDER BY version', doc
        )
        assert [(item['version'], item['markdown']) for item in versions] == [(1, '# One'), (2, '# Two')]
        assert versions[0]['id'] == first
        current = row(
            conn,
            """SELECT v.id,v.markdown FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id
            WHERE d.id=%s""",
            doc,
        )
        assert current == {'id': versions[1]['id'], 'markdown': '# Two'}
        drafts = {
            item['id']: item
            for item in rows(conn, 'SELECT id,document_id,status,base_version_id,origin,slug FROM ka.document_drafts')
        }
        assert drafts[draft] == {
            'id': draft,
            'document_id': doc,
            'status': 'open',
            'base_version_id': first,
            'origin': 'agent',
            'slug': None,
        }
        assert drafts[new]['slug'] == 'disk-layout'
        resource_ids = row(conn, 'SELECT resource_ids FROM ka.document_drafts WHERE id=%s', new)['resource_ids']
        assert resource_ids == {key: [] for key in ARRAYS.split(',')} | {'server_ids': [str(ids['server'])]}
        sources = rows(conn, 'SELECT draft_id,version_id,source_document_id,source_version_id FROM ka.document_sources')
        # The version it read is found again by number (the live version is a new row).
        read = row(conn, 'SELECT current_version_id FROM ka.documents WHERE id=%s', source)['current_version_id']
        assert sources == [
            {'draft_id': draft, 'version_id': None, 'source_document_id': source, 'source_version_id': read}
        ]
        # The live versions are new rows (new ids); everything else in the old view is as it was.
        assert [
            item | {'current_version_id': None}
            for item in rows(conn, 'SELECT * FROM ka.document_details ORDER BY slug')
        ] == [item | {'current_version_id': None} for item in details]
        assert schema(conn) == snapshot


def test_rollback_keeps_the_source_version_a_draft_read(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        doc, (first, _) = old_document(conn, project, 'runbook', ['# One', '# Two'])
        source, (older, _) = old_document(conn, project, 'overview', ['# Overview', '# Overview 2'])
        draft = old_draft(conn, project, document_id=doc, base_version_id=first)
        conn.execute(
            """INSERT INTO ka.document_sources(project_id,draft_id,kind,source_document_id,source_version_id,
            source_title,source_version) VALUES (%s,%s,'read',%s,%s,'Overview',1)""",
            (project, draft, source, older),
        )
        conn.commit()
        run(conn, UP)
        conn.commit()
        run(conn, DOWN)
        assert row(conn, 'SELECT source_version_id FROM ka.document_sources')['source_version_id'] == older


def test_rollback_refuses_when_link_history_would_be_lost(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        before(conn)
        ids = resources(conn, project)
        doc, _ = old_document(conn, project, 'runbook', ['# One'], links=all_links(ids)[:1])
        person = actor(conn, store)
        conn.commit()
        run(conn, UP)
        for change in (
            ('UPDATE ka.document_resources SET removed_at=now(),removed_by=%s WHERE document_id=%s', (person, doc)),
            (
                """INSERT INTO ka.document_resources(project_id,document_id,service_id,connection,added_how,added_by)
                VALUES (%s,%s,%s,'linked','person',%s)""",
                (project, doc, ids['service'], person),
            ),
        ):
            conn.execute(*change)
            conn.commit()
            with pytest.raises(psycopg.errors.RaiseException, match='would be lost'):
                run(conn, DOWN)
            conn.rollback()
            conn.execute(
                """DELETE FROM ka.document_resources WHERE document_id=%s
                AND added_how <> 'migrated'""",
                (doc,),
            )
            conn.execute(
                'UPDATE ka.document_resources SET removed_at=NULL,removed_by=NULL WHERE document_id=%s', (doc,)
            )
            conn.commit()
        run(conn, DOWN)
        assert row(conn, 'SELECT count(*) AS n FROM ka.document_environments')['n'] == 1
