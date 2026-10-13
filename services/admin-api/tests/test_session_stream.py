"""The session event stream on the hub: keepalive, resync and resume, no database connection per open stream, a
worker's flush reaching an open stream without polling, open streams ending when the app stops, and 503 when the hub
is not running."""
import threading
import time
from uuid import UUID

from knowledge_admin.sessions import api as sessions_api
from knowledge_admin.live import session_hub
from knowledge_admin.run_record import RunRecorder
from test_generations import live, parse, record, sessions, start  # noqa: F401  (fixtures)
from test_repositories import HEADERS


def wait_until(check, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, 'condition not reached'
        time.sleep(0.02)


def open_stream(client, sid: str, headers=HEADERS):
    """GET the stream in a thread (TestClient returns only when the stream ends); the returned function joins it and
    gives the response."""
    result = {}
    thread = threading.Thread(target=lambda: result.update(
        response=client.get(f'/api/sessions/{sid}/events', headers=headers)))
    thread.start()

    def finish(timeout: float = 10.0):
        thread.join(timeout)
        assert not thread.is_alive(), 'the stream did not end'
        return result['response']
    return finish


def working(store, sid: str) -> None:
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='working',turns=1 WHERE id=%s", (sid,))


def close(store, sid: str) -> None:
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='closed',closed_at=now() WHERE id=%s", (sid,))


def test_a_quiet_stream_sends_keepalives(sessions, live, monkeypatch):
    monkeypatch.setattr(sessions_api, 'KEEPALIVE_SECONDS', 0.1)
    sid = start(sessions)['id']
    finish = open_stream(sessions.client, sid)
    wait_until(lambda: live.subscribers(UUID(sid)) == 1)
    time.sleep(0.5)
    close(sessions.store, sid)
    response = finish()
    assert response.text.count(': keepalive') >= 2
    assert parse(response.text)[-1]['event'] == 'closed'


def test_a_stream_that_falls_behind_ends_with_retry_and_resumes_from_its_last_id(sessions, live, monkeypatch):
    monkeypatch.setattr(session_hub, 'QUEUE_SIZE', 2)
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    working(store, sid)
    with store.connect() as conn:
        for seq in range(1, 6):
            record(conn, sid, 1, seq, 'message', {'text': f'part {seq}'})
    first = client.get(f'/api/sessions/{sid}/events', headers=HEADERS)
    runs = [e for e in parse(first.text) if e['event'] == 'run_event']
    assert [e['data']['seq'] for e in runs] == [1, 2]
    assert first.text.endswith('retry: 1000\n\n')  # the browser reconnects with Last-Event-ID
    monkeypatch.setattr(session_hub, 'QUEUE_SIZE', 500)
    close(store, sid)
    resumed = client.get(f'/api/sessions/{sid}/events',
                         headers={**HEADERS, 'Last-Event-ID': str(runs[-1]['id'])})
    events = parse(resumed.text)
    assert [e['data']['seq'] for e in events if e['event'] == 'run_event'] == [3, 4, 5]
    assert events[-1]['event'] == 'closed'


def test_open_streams_hold_no_database_connection(sessions, live):
    store = sessions.store
    sid = start(sessions)['id']
    finishers = [open_stream(sessions.client, sid) for _ in range(10)]
    wait_until(lambda: live.subscribers(UUID(sid)) == 10)

    def connections() -> int:
        with store.connect() as conn:
            return conn.execute('''SELECT count(*) AS n FROM pg_stat_activity
                WHERE datname=current_database() AND pid<>pg_backend_pid()
                AND backend_type='client backend' ''').fetchone()['n']
    wait_until(lambda: connections() <= 2)  # the listener, and the reader's only while it reads
    for _ in range(5):
        assert connections() <= 2
        time.sleep(0.1)
    close(store, sid)
    for finish in finishers:
        assert parse(finish().text)[-1]['event'] == 'closed'
    assert live.subscribers(UUID(sid)) == 0


def test_a_worker_flush_reaches_an_open_stream_without_polling(sessions, live):
    store = sessions.store
    sid = start(sessions)['id']
    working(store, sid)
    finish = open_stream(sessions.client, sid)
    wait_until(lambda: live.subscribers(UUID(sid)) == 1)
    wait_until(lambda: live.reads >= 1)  # the opening read
    reads = live.reads
    recorder = RunRecorder(run_key=f'cc:{sid}:1', project_id=None, agent_id='ad-hoc', actor_id=None, secrets=[])
    recorder.add('message', {'text': 'Flushed by the worker'})
    recorder.flush()
    wait_until(lambda: live.reads > reads, timeout=2)  # the notice: the 30 s check is far away
    close(store, sid)
    events = parse(finish().text)
    assert [e['data']['payload']['text'] for e in events if e['event'] == 'run_event'] == ['Flushed by the worker']
    assert events[-1]['event'] == 'closed'


def test_stopping_the_app_ends_open_streams_so_the_browser_reconnects(sessions):
    sid = start(sessions)['id']
    with sessions.client as client:
        hub = client.app.state.session_hub
        finish = open_stream(client, sid)
        wait_until(lambda: hub.subscribers(UUID(sid)) == 1)
        wait_until(lambda: hub.reads >= 1)
    response = finish(timeout=5)  # the session is still open: only the app stopped
    assert response.text.endswith('retry: 1000\n\n')
    assert 'event: closed' not in response.text
    assert hub.subscribers(UUID(sid)) == 0


def test_the_stream_answers_503_when_the_hub_is_not_running(sessions):
    message = sessions_api.LIVE_UNAVAILABLE
    sid = start(sessions)['id']  # the client's lifespan never ran
    response = sessions.client.get(f'/api/sessions/{sid}/events', headers=HEADERS)
    assert response.status_code == 503
    assert response.json()['error']['message'] == message
