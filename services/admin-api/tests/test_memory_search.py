"""memory/search.py: full text and vector distance merged by reciprocal rank, filtered by metadata and agent."""

from knowledge_admin.memory import store as memory
from knowledge_admin.memory.search import count, not_embedded, search


def add(conn, pid, name, content, metadata=None, vector=None, profile='p3', agent=None):
    return memory.create(
        conn, pid, name, metadata or {}, content, memory.Author(agent_id=agent), vector, profile if vector else None
    )


def names(rows) -> list[str]:
    return [row['name'] for row in rows]


def an_agent(conn, name: str) -> str:
    return conn.execute(
        "INSERT INTO ka.agents(id,name,system_prompt) VALUES (%s,%s,'x') RETURNING id", (name.lower(), name)
    ).fetchone()['id']


def test_words_meaning_and_both(store):
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        add(conn, pid, 'deploy', 'Deploy payments with make release.', vector=[1.0, 0.0, 0.0])
        add(conn, pid, 'queue', 'The billing queue retries five times.', vector=[0.0, 1.0, 0.0])
        add(conn, pid, 'owner', 'Ana owns the payments service.', vector=[0.9, 0.1, 0.0])
        assert names(search(conn, pid, query='retries')) == ['queue']
        by_meaning = search(conn, pid, query='zzz', vector=[1.0, 0.0, 0.0], profile='p3')
        assert names(by_meaning)[:2] == ['deploy', 'owner']
        both = search(conn, pid, query='make payments', vector=[1.0, 0.0, 0.0], profile='p3')
        assert names(both)[0] == 'deploy'  # first in both rankings
        assert both[0]['score'] > both[1]['score']


def test_metadata_filters_need_every_pair(store):
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        add(conn, pid, 'a', 'text', {'service': 'payments', 'kind': 'runbook'})
        add(conn, pid, 'b', 'text', {'service': 'payments'})
        add(conn, pid, 'c', 'text', {'service': 'billing', 'kind': 'runbook'})
        assert sorted(names(search(conn, pid, metadata={'service': 'payments'}))) == ['a', 'b']
        assert names(search(conn, pid, metadata={'service': 'payments', 'kind': 'runbook'})) == ['a']
        assert names(search(conn, pid, query='text', metadata={'kind': 'runbook'}, limit=5)) in (['a', 'c'], ['c', 'a'])


def test_agent_filter_matches_creator_or_last_editor(store):
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        coder, triage = an_agent(conn, 'Coder'), an_agent(conn, 'Triage')
        one = add(conn, pid, 'one', 'text', agent=coder)
        add(conn, pid, 'two', 'text', agent=triage)
        memory.update(conn, pid, one['id'], 'one', {}, 'text 2', 1, memory.Author(agent_id=triage), None, None)
        assert sorted(names(search(conn, pid, agent_id=triage))) == ['one', 'two']
        assert names(search(conn, pid, agent_id=coder)) == ['one']


def test_no_query_lists_newest_first_with_paging(store):
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        for name, hours in [('a', 2), ('b', 3), ('c', 1)]:
            add(conn, pid, name, 'text')
            conn.execute(
                "UPDATE ka.memories SET updated_at = now() - %s * interval '1 hour' WHERE name=%s", (hours, name)
            )
        assert names(search(conn, pid)) == ['c', 'a', 'b']
        assert names(search(conn, pid, limit=2)) == ['c', 'a']
        assert names(search(conn, pid, limit=2, offset=2)) == ['b']


def test_vectors_of_another_profile_are_ignored_even_with_other_dimensions(store):
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        add(conn, pid, 'old', 'old text', vector=[1.0, 0.0], profile='p2')  # another model, 2 dimensions
        add(conn, pid, 'new', 'new text', vector=[1.0, 0.0, 0.0], profile='p3')
        assert names(search(conn, pid, query='zzz', vector=[1.0, 0.0, 0.0], profile='p3')) == ['new']
        assert not_embedded(conn, pid, 'p3') == 1
        assert not_embedded(conn, pid, None) == 2


def test_without_vectors_search_is_full_text_only(store):
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        add(conn, pid, 'deploy', 'Deploy payments.')
        assert names(search(conn, pid, query='payments', vector=None)) == ['deploy']


def test_a_query_of_only_punctuation_finds_nothing_without_error(store):
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        add(conn, pid, 'deploy', 'Deploy payments.')
        assert search(conn, pid, query='/') == []
        assert search(conn, pid, query='--') == []


def test_count_is_how_many_entries_search_can_page_through(store):
    with store.connect() as conn:
        pid = store.project(conn, 'shop')
        add(conn, pid, 'deploy', 'Deploy payments.', vector=[1.0, 0.0, 0.0])
        add(conn, pid, 'queue', 'Billing queue.', vector=[0.0, 1.0, 0.0])
        add(conn, pid, 'old', 'Old payments.', vector=[1.0, 0.0], profile='p2')
        add(conn, pid, 'plain', 'No vector.')
        assert count(conn, pid) == 4
        assert count(conn, pid, query='payments') == 2  # words only
        # with a vector every entry of its profile is ranked, plus the word matches of other profiles
        assert count(conn, pid, query='payments', profile='p3') == 3
        assert len(search(conn, pid, query='payments', vector=[1.0, 0.0, 0.0], profile='p3', limit=10)) == 3
        assert count(conn, pid, query='/') == 0
