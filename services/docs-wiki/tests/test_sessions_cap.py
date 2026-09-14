"""The HISTORY_CAP window must never orphan tool outputs.

Field incident 2026-08-05: a session with 46 stored items 400'd on every
later turn — the last-40 slice began with a function_call_output whose
function_call fell outside the window, and the chat-completions converter
replayed it as a bare role:"tool" message ("messages with role 'tool' must
be a response to a preceding message with 'tool_calls'").
"""
import asyncio

from agents import SQLiteSession

from docs_wiki import sessions


def _msg(role, text):
    return {"role": role, "content": text}


def _call(call_id):
    return {"type": "function_call", "call_id": call_id,
            "name": "search_docs", "arguments": "{}"}


def _output(call_id):
    return {"type": "function_call_output", "call_id": call_id,
            "output": "results"}


def _window(tmp_path, items):
    async def run():
        session = sessions.open_session(tmp_path, "k" * 12)
        await session.add_items(items)
        return await session.get_items()
    return asyncio.run(run())


def test_window_drops_leading_orphan_output(tmp_path, monkeypatch):
    # Field shape: the slice starts on an output whose call is outside
    # the window (6 items, cap 4 -> window = last 4, beginning on the
    # output of a call that was truncated away).
    monkeypatch.setattr(sessions, "HISTORY_CAP", 4)
    items = [_msg("user", "q1"), _call("c1"), _output("c1"),
             _msg("assistant", "a1"), _msg("user", "q2"),
             _msg("assistant", "a2")]
    got = _window(tmp_path, items)
    assert [i.get("content") for i in got] == ["a1", "q2", "a2"]
    assert all(i.get("type") != "function_call_output" for i in got)


def test_window_drops_mid_window_orphan_from_parallel_calls(tmp_path, monkeypatch):
    # Parallel calls split by the boundary: c1's call is outside, c2's is
    # inside; c1's output is mid-window but unmatched and must go, c2's
    # stays.
    monkeypatch.setattr(sessions, "HISTORY_CAP", 4)
    items = [_msg("user", "q"), _call("c1"), _call("c2"),
             _output("c1"), _output("c2"), _msg("assistant", "a")]
    got = _window(tmp_path, items)
    outputs = [i for i in got if i.get("type") == "function_call_output"]
    assert [o.get("call_id") for o in outputs] == ["c2"]
    assert got[0].get("call_id") == "c2"


def test_intact_window_unchanged(tmp_path):
    items = [_msg("user", "q"), _call("c1"), _output("c1"),
             _msg("assistant", "a")]
    got = _window(tmp_path, items)
    assert got == items


def test_full_history_reads_stay_complete(tmp_path, monkeypatch):
    # api.py's transcript path reads via the BASE class with limit=None —
    # storage keeps everything regardless of the window filter.
    monkeypatch.setattr(sessions, "HISTORY_CAP", 2)
    items = [_msg("user", "q"), _call("c1"), _output("c1"),
             _msg("assistant", "a")]

    async def run():
        session = sessions.open_session(tmp_path, "k" * 12)
        await session.add_items(items)
        return await SQLiteSession.get_items(session, None)
    got = asyncio.run(run())
    assert len(got) == 4
