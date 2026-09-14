import asyncio
import sqlite3

from docs_wiki import sessions


def test_new_key_format_and_uniqueness():
    keys = {sessions.new_key() for _ in range(50)}
    assert len(keys) == 50
    for key in keys:
        assert sessions.KEY_RE.fullmatch(key)


def _add(state_dir, key, items):
    session = sessions.open_session(state_dir, key)
    asyncio.run(session.add_items(items))


def test_roundtrip_transcript_and_exists(tmp_path):
    key = sessions.new_key()
    assert not sessions.exists(tmp_path, key)
    _add(tmp_path, key, [
        {"role": "user", "content": "how do refunds work?"},
        {"type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "Async via events [1]."}]},
        {"type": "function_call", "name": "search_docs",
         "arguments": "{}", "call_id": "c1"},
    ])
    assert sessions.exists(tmp_path, key)
    messages = asyncio.run(sessions.transcript(tmp_path, key))
    assert messages == [
        {"role": "user", "content": "how do refunds work?"},
        {"role": "assistant", "content": "Async via events [1]."},
    ]
    assert (tmp_path / "sessions.db").is_file()


def test_delete(tmp_path):
    key = sessions.new_key()
    _add(tmp_path, key, [{"role": "user", "content": "hi"}])
    asyncio.run(sessions.delete(tmp_path, key))
    assert not sessions.exists(tmp_path, key)


def test_history_cap(tmp_path):
    key = sessions.new_key()
    _add(tmp_path, key, [{"role": "user", "content": f"m{i}"}
                          for i in range(50)])
    session = sessions.open_session(tmp_path, key)
    items = asyncio.run(session.get_items())
    assert len(items) == sessions.HISTORY_CAP
    assert items[-1]["content"] == "m49"
    # explicit limit still honored
    assert len(asyncio.run(session.get_items(limit=5))) == 5


def test_transcript_returns_full_history_beyond_cap(tmp_path):
    key = sessions.new_key()
    _add(tmp_path, key, [{"role": "user", "content": f"m{i}"}
                          for i in range(50)])
    messages = asyncio.run(sessions.transcript(tmp_path, key))
    assert len(messages) == 50
    assert messages[0]["content"] == "m0"
    assert messages[-1]["content"] == "m49"
    session = sessions.open_session(tmp_path, key)
    assert len(asyncio.run(session.get_items())) == sessions.HISTORY_CAP


def test_prune_zero_message_session(tmp_path):
    empty_key, fresh_key = sessions.new_key(), sessions.new_key()
    _add(tmp_path, empty_key, [{"role": "user", "content": "will be removed"}])
    session = sessions.open_session(tmp_path, empty_key)
    asyncio.run(session.pop_item())
    _add(tmp_path, fresh_key, [{"role": "user", "content": "still here"}])
    db = sessions.db_path(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE agent_sessions SET created_at = datetime('now', '-100 days') "
            "WHERE session_id = ?", (empty_key,))
    assert sessions.exists(tmp_path, empty_key)
    assert sessions.prune(tmp_path, 90) == 1
    assert not sessions.exists(tmp_path, empty_key)
    assert sessions.exists(tmp_path, fresh_key)


def test_prune(tmp_path):
    old, new = sessions.new_key(), sessions.new_key()
    _add(tmp_path, old, [{"role": "user", "content": "old"}])
    _add(tmp_path, new, [{"role": "user", "content": "new"}])
    db = sessions.db_path(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE agent_messages SET created_at = datetime('now', '-100 days') "
            "WHERE session_id = ?", (old,))
        conn.execute(
            "UPDATE agent_sessions SET created_at = datetime('now', '-100 days') "
            "WHERE session_id = ?", (old,))
    assert sessions.prune(tmp_path, 90) == 1
    assert not sessions.exists(tmp_path, old)
    assert sessions.exists(tmp_path, new)
    assert sessions.prune(tmp_path, 0) == 0      # disabled
    assert sessions.prune(tmp_path / "missing", 90) == 0  # no db → no-op


def test_transcript_derives_sources_used_from_tool_calls(tmp_path):
    key = sessions.new_key()
    _add(tmp_path, key, [
        {"role": "user", "content": "how do refunds work?"},
        {"type": "function_call", "name": "search_docs",
         "arguments": "{}", "call_id": "c1"},
        {"type": "function_call", "name": "read_doc",
         "arguments": "{}", "call_id": "c2"},
        {"type": "function_call", "name": "ask_code",
         "arguments": "{}", "call_id": "c3"},
        {"type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "Async via events."}]},
    ])
    messages = asyncio.run(sessions.transcript(tmp_path, key))
    assert messages == [
        {"role": "user", "content": "how do refunds work?"},
        # deduped (search_docs + read_doc -> one "docs") and sorted
        {"role": "assistant", "content": "Async via events.",
         "sources_used": ["codex", "docs"]},
    ]


def test_transcript_sources_reset_between_turns(tmp_path):
    key = sessions.new_key()
    _add(tmp_path, key, [
        {"role": "user", "content": "q1"},
        {"type": "function_call", "name": "ask_code",
         "arguments": "{}", "call_id": "c1"},
        {"type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "a1"}]},
        {"role": "user", "content": "q2"},
        {"type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "a2"}]},
    ])
    messages = asyncio.run(sessions.transcript(tmp_path, key))
    assert messages[1] == {"role": "assistant", "content": "a1",
                           "sources_used": ["codex"]}
    # second turn used no tools: no key at all, not an empty list
    assert messages[3] == {"role": "assistant", "content": "a2"}


def test_transcript_ignores_unknown_tools_and_orphaned_calls(tmp_path):
    key = sessions.new_key()
    _add(tmp_path, key, [
        {"role": "user", "content": "q1"},
        {"type": "function_call", "name": "search_docs",
         "arguments": "{}", "call_id": "c1"},
        # turn errored before an assistant message was stored; the next
        # user message must reset accumulated sources
        {"role": "user", "content": "q2"},
        {"type": "function_call", "name": "some_future_tool",
         "arguments": "{}", "call_id": "c2"},
        {"type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "a2"}]},
    ])
    messages = asyncio.run(sessions.transcript(tmp_path, key))
    # unknown tool ignored and c1's "docs" did not leak across the user turn
    assert messages == [
        {"role": "user", "content": "q1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]


def test_context_binding_is_immutable_and_deleted_with_chat(tmp_path):
    key = sessions.new_key()
    assert sessions.context(tmp_path, key) is None
    assert sessions.bind_context(tmp_path, key, {'folders': ['payments']})
    assert sessions.exists(tmp_path, key)
    assert asyncio.run(sessions.transcript(tmp_path, key)) == []
    assert not sessions.bind_context(tmp_path, key, {'folders': None})
    assert sessions.context(tmp_path, key) == {'folders': ['payments']}
    asyncio.run(sessions.delete(tmp_path, key))
    assert sessions.context(tmp_path, key) is None
