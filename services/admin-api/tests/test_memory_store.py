"""memory/store.py: every write keeps history and records an event; names are unique; revisions guard updates."""

import threading
from uuid import uuid4

import pytest
from knowledge_admin.memory import store as memory
from knowledge_admin.memory.embedding import embed_text

AGENT = memory.Author()
VECTOR, PROFILE = [1.0, 0.0, 0.0], 'profile-a'


def project(store):
    with store.connect() as conn:
        return store.project(conn, 'shop')


def events(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute(
            "SELECT type,subject,source,source_id,payload FROM ka.events WHERE source='memory' ORDER BY id"
        ).fetchall()


def saved(store, name='payments/deploy', content='Deploy with make release.', metadata=None, **values) -> dict:
    with store.connect() as conn:
        return memory.create(
            conn,
            store.project(conn, 'shop'),
            name,
            metadata or {'service': 'payments'},
            content,
            AGENT,
            values.get('vector'),
            values.get('profile'),
        )


def test_create_returns_the_entry_and_records_an_event(store):
    entry = saved(store, vector=VECTOR, profile=PROFILE)
    assert (entry['name'], entry['revision'], entry['metadata'], entry['embedding_profile']) == (
        'payments/deploy',
        1,
        {'service': 'payments'},
        PROFILE,
    )
    [event] = events(store)
    assert (event['type'], event['subject'], event['source_id']) == ('memory.saved', 'payments/deploy', entry['id'])
    assert event['payload'] == {
        'memory_id': str(entry['id']),
        'name': 'payments/deploy',
        'metadata': {'service': 'payments'},
        'revision': 1,
        'agent_id': None,
        'session_id': None,
        'actor': None,
    }


def test_an_existing_name_is_refused_with_the_existing_entry(store):
    first = saved(store)
    with pytest.raises(memory.Exists) as refused:
        saved(store, content='Other text')
    assert refused.value.entry['id'] == first['id'] and refused.value.entry['content'] == 'Deploy with make release.'


def test_two_saves_of_one_new_name_at_once_give_one_exists(store):
    project(store)  # the project row exists before the race: only the memory name is contended
    results, barrier = [], threading.Barrier(2)

    def save():
        barrier.wait()
        try:
            results.append(saved(store)['id'])
        except memory.Exists as error:
            results.append(('exists', error.entry['id']))

    threads = [threading.Thread(target=save) for _ in range(2)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    created = [result for result in results if not isinstance(result, tuple)]
    assert len(created) == 1 and results.count(('exists', created[0])) == 1


def test_update_keeps_the_earlier_revision_and_refuses_a_stale_one(store):
    entry = saved(store)
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        updated = memory.update(
            conn,
            pid,
            entry['id'],
            entry['name'],
            {'service': 'payments', 'kind': 'runbook'},
            'Use make release-2.',
            1,
            AGENT,
            None,
            None,
        )
        assert updated['revision'] == 2
        [old] = memory.versions(conn, entry['id'])
        assert (old['revision'], old['content']) == (1, 'Deploy with make release.')
        with pytest.raises(memory.Stale) as stale:
            memory.update(conn, pid, entry['id'], entry['name'], {}, 'x', 1, AGENT, None, None)
        assert stale.value.entry['revision'] == 2
    assert [e['type'] for e in events(store)] == ['memory.saved', 'memory.updated']


def test_restore_makes_a_new_revision_from_an_old_one(store):
    entry = saved(store)
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        memory.update(conn, pid, entry['id'], entry['name'], {}, 'Second.', 1, AGENT, None, None)
        restored = memory.restore(conn, pid, entry['id'], 1, 2, AGENT, None, None)
        assert (restored['revision'], restored['content'], restored['metadata']) == (
            3,
            'Deploy with make release.',
            {'service': 'payments'},
        )
        assert [v['revision'] for v in memory.versions(conn, entry['id'])] == [2, 1]
    assert events(store)[-1]['type'] == 'memory.restored'


def test_restore_onto_a_name_now_taken_changes_nothing(store):
    entry = saved(store)  # payments/deploy, revision 1
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        memory.update(conn, pid, entry['id'], 'payments/renamed', {}, 'x', 1, AGENT, None, None)  # revision 2
        memory.create(conn, pid, 'payments/deploy', {}, 'Another entry took the old name.', AGENT, None, None)
        with pytest.raises(memory.Exists) as taken:
            memory.restore(conn, pid, entry['id'], 1, 2, AGENT, None, None)
        assert taken.value.entry['content'] == 'Another entry took the old name.'
        current = memory.get(conn, pid, memory_id=entry['id'])
        assert (current['name'], current['revision']) == ('payments/renamed', 2)
        assert [v['revision'] for v in memory.versions(conn, entry['id'])] == [1]


def test_delete_removes_entry_and_history_and_records_an_event(store):
    entry = saved(store)
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        with pytest.raises(memory.Stale):
            memory.delete(conn, pid, entry['id'], 2, AGENT)
        memory.delete(conn, pid, entry['id'], 1, memory.Author(actor_id=None))
        assert memory.get(conn, pid, memory_id=entry['id']) is None
        assert memory.versions(conn, entry['id']) == []
    assert events(store)[-1]['type'] == 'memory.deleted'


def test_unknown_entry_is_missing(store):
    with store.connect() as conn, pytest.raises(memory.Missing):
        memory.update(conn, store.project(conn, 'shop'), uuid4(), 'a', {}, 'x', 1, AGENT, None, None)


@pytest.mark.parametrize(
    ('name', 'metadata', 'content', 'message'),
    [
        ('Payments', {}, 'x', 'A name is'),
        ('a', {f'k{n}': 'v' for n in range(21)}, 'x', 'at most 20 keys'),
        ('a', {'Bad': 'v'}, 'x', 'Metadata key "Bad"'),
        ('a', {'port': 8080}, 'x', 'Metadata value of "port"'),
        ('a', {'x': None}, 'x', 'Metadata value of "x"'),
        ('a', {'x': {'nested': 'no'}}, 'x', 'Metadata value of "x"'),
        ('a', {'x': ''}, 'x', 'Metadata value of "x"'),
        ('a', {}, '   ', 'Content must be'),
        ('a', {}, 'x' * 20001, 'Content must be'),
        ('a', {}, 'a\x00b', 'NUL'),
    ],
)
def test_validation(name, metadata, content, message):
    with pytest.raises(memory.Invalid) as error:
        memory.validate(name, metadata, content)
    assert message in str(error.value)


def test_embed_text_without_a_model_or_on_failure_gives_no_vector(monkeypatch):
    assert embed_text(None, 'x') == (None, None)

    def failing(*_):
        from knowledge_llm import ModelUnavailable

        raise ModelUnavailable('down')

    monkeypatch.setattr('knowledge_admin.memory.embedding.embeddings.embed', failing)
    assert embed_text(object(), 'x') == (None, None)


def test_reembed_embeds_what_has_no_vector_of_the_current_profile(store, monkeypatch):
    saved(store, name='a')
    saved(store, name='b', vector=[0.0, 1.0], profile='old-profile')
    monkeypatch.setattr('knowledge_admin.memory.store.current_model', lambda conn, project_id: object())
    monkeypatch.setattr(
        'knowledge_admin.memory.store.embeddings.embed',
        lambda model, texts, transport=None: [[1.0, 0.0, 0.0]] * len(texts),
    )
    monkeypatch.setattr('knowledge_admin.memory.store.embeddings.embedding_profile', lambda model: PROFILE)
    assert memory.reembed(store, project(store)) == {'embedded': 2, 'failed': 0}
    with store.connect() as conn:
        rows = conn.execute('SELECT name,embedding_profile,revision FROM ka.memories ORDER BY name').fetchall()
    assert [(r['name'], r['embedding_profile'], r['revision']) for r in rows] == [('a', PROFILE, 1), ('b', PROFILE, 1)]
    assert len(events(store)) == 2  # re-embedding is not a change: no new revision, no event
