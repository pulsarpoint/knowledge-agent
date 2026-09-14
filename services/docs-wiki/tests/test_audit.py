import sqlite3
from contextlib import closing

from docs_wiki import audit


def _row(**overrides):
    row = dict(user_email="a@x.io", session_key="k1", question="q?",
               answer="a.", sources_used=["docs"], not_covered=False,
               escalated=False)
    row.update(overrides)
    return row


def test_append_and_query_roundtrip(tmp_path):
    audit.append(tmp_path, **_row())
    rows = audit.query(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["user_email"] == "a@x.io"
    assert row["session_key"] == "k1"
    assert row["question"] == "q?"
    assert row["answer"] == "a."
    assert row["sources_used"] == ["docs"]
    assert row["not_covered"] is False
    assert row["escalated"] is False
    assert isinstance(row["id"], int) and row["ts"]


def test_query_newest_first_with_user_filter_and_cursor(tmp_path):
    for i in range(5):
        audit.append(tmp_path, **_row(question=f"q{i}",
                                      user_email="a@x.io" if i % 2 == 0 else "b@x.io"))
    rows = audit.query(tmp_path)
    assert [r["question"] for r in rows] == ["q4", "q3", "q2", "q1", "q0"]
    a_rows = audit.query(tmp_path, user="a@x.io")
    assert [r["question"] for r in a_rows] == ["q4", "q2", "q0"]
    # cursor: rows strictly older than the newest one
    older = audit.query(tmp_path, before_id=rows[0]["id"])
    assert [r["question"] for r in older] == ["q3", "q2", "q1", "q0"]
    assert len(audit.query(tmp_path, limit=2)) == 2


def test_by_session_oldest_first_across_other_sessions(tmp_path):
    audit.append(tmp_path, **_row(session_key="s1", question="first"))
    audit.append(tmp_path, **_row(session_key="other"))
    audit.append(tmp_path, **_row(session_key="s1", question="second",
                                  escalated=True))
    rows = audit.by_session(tmp_path, "s1")
    assert [r["question"] for r in rows] == ["first", "second"]
    assert rows[1]["escalated"] is True


def test_query_empty_dir_returns_empty(tmp_path):
    assert audit.query(tmp_path / "nope") == []
    assert audit.by_session(tmp_path / "nope", "k") == []


def test_users_aggregates_and_orders_by_recency(tmp_path):
    audit.append(tmp_path, **_row(user_email="a@x.io", session_key="s1"))
    audit.append(tmp_path, **_row(user_email="b@x.io", session_key="s2"))
    audit.append(tmp_path, **_row(user_email="a@x.io", session_key="s3"))
    users = audit.users(tmp_path)
    # a@x.io wrote last (row id 3) so sorts first
    assert [u["user_email"] for u in users] == ["a@x.io", "b@x.io"]
    a = users[0]
    assert a["sessions"] == 2 and a["questions"] == 2 and a["last_ts"]
    assert users[1]["sessions"] == 1 and users[1]["questions"] == 1


def test_sessions_for_groups_and_first_question(tmp_path):
    audit.append(tmp_path, **_row(user_email="a@x.io", session_key="s1",
                                  question="first q"))
    audit.append(tmp_path, **_row(user_email="a@x.io", session_key="s1",
                                  question="second q"))
    audit.append(tmp_path, **_row(user_email="b@x.io", session_key="other"))
    audit.append(tmp_path, **_row(user_email="a@x.io", session_key="s2",
                                  question="newer session"))
    sessions = audit.sessions_for(tmp_path, "a@x.io")
    # s2 has the most recent row, so it sorts first; other-user session absent
    assert [s["session_key"] for s in sessions] == ["s2", "s1"]
    s1 = sessions[1]
    assert s1["first_question"] == "first q"
    assert s1["questions"] == 2
    assert s1["first_ts"] and s1["last_ts"]


def test_aggregates_empty_dir(tmp_path):
    assert audit.users(tmp_path / "nope") == []
    assert audit.sessions_for(tmp_path / "nope", "a@x.io") == []


def test_indexes_exist_for_admin_and_per_user_filters(tmp_path):
    audit.append(tmp_path, **_row())
    with closing(sqlite3.connect(tmp_path / "audit.db")) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_audit_session" in names
    assert "idx_audit_user" in names
