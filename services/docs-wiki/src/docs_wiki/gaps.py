"""Gap capture and reading: `<state_dir>/<app>/gaps.jsonl` (append-only
user data, under docs_wiki.config.state_root's central state directory)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def append_gap(state_app_dir: Path, question: str, near: list[dict], *,
               top_score: float | None = None,
               results: int | None = None,
               session_ref: str | None = None) -> None:
    state_app_dir.mkdir(parents=True, exist_ok=True)
    entry = {"question": question,
             "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "near_misses": [{"path": c["path"],
                              "heading_path": c["heading_path"]}
                             for c in near[:3]]}
    # Telemetry keys are added only when available (extra keys, format
    # otherwise unchanged): `results` is real from the day search hits
    # exist; `top_score` stays absent until search hits carry a "score".
    # `session_ref` follows the same rule — present only when the caller
    # (the api layer, which alone holds the raw session key) computed one;
    # it is a one-way hash short-code, never the raw key itself.
    if top_score is not None:
        entry["top_score"] = top_score
    if results is not None:
        entry["results"] = results
    if session_ref is not None:
        entry["session_ref"] = session_ref
    with (state_app_dir / "gaps.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def append_code_answer(state_app_dir: Path, question: str,
                       session_ref: str | None, answer: str,
                       citations: list[dict], not_found: bool) -> None:
    """Records a code-agent answer given in response to an escalated
    question. Not a gap in its own right — an answer, not an open
    question — so `load_gaps` below folds each of these onto the prior
    NOT_COVERED gap it answers (matched by question + session_ref)
    rather than surfacing it as a standalone entry."""
    state_app_dir.mkdir(parents=True, exist_ok=True)
    entry = {"kind": "code-answer",
             "question": question,
             "session_ref": session_ref,
             "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "answer": answer,
             "citations": citations,
             "not_found": not_found}
    with (state_app_dir / "gaps.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def load_gaps(state_app_dir: Path) -> tuple[list[dict], int]:
    """Parsed gap entries plus the count of skipped malformed lines.

    "code-answer" records (see append_code_answer) never appear in the
    returned list themselves: each folds onto the LAST prior gap entry
    sharing its (question, session_ref) as that entry's `code_answer`
    key, in file order — a code-answer with no matching earlier gap
    (the state it refers to doesn't exist, e.g. gaps.jsonl was
    truncated/rotated) counts as skipped, same as a malformed line.
    """
    gaps_file = state_app_dir / "gaps.jsonl"
    gaps: list[dict] = []
    skipped = 0
    if gaps_file.is_file():
        latest_by_key: dict[tuple, dict] = {}
        for line in gaps_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(entry, dict):
                skipped += 1
                continue
            if entry.get("kind") == "code-answer":
                target = latest_by_key.get(
                    (entry.get("question"), entry.get("session_ref")))
                if target is None:
                    skipped += 1
                    continue
                target["code_answer"] = {
                    "answer": entry.get("answer"),
                    "citations": entry.get("citations"),
                    "not_found": entry.get("not_found"),
                    "timestamp": entry.get("timestamp"),
                }
                continue
            gaps.append(entry)
            latest_by_key[(entry.get("question"), entry.get("session_ref"))] = entry
    return gaps, skipped
