# Session Review (Section 2) — Design

Direction approved in conversation 2026-10-28. Builds on Section 1, the session explorer (`sessions/explorer.py`, merged
1af8c1e).

## Goal

After an agent session finishes, a person gives feedback and talks it through with a **reviewer** in a chat that looks
and behaves exactly like the generic agent chat: every model message, tool call and result is visible as it happens.
The reviewer explores the reviewed session with the explorer tools, says **where** the improvement belongs — the
agent's **instructions**, a **skill**, or **memory** — and why, and the person answers in the chat ("yes", "make it a
memory note instead", "change the wording to …"). Only after the person agrees does the reviewer make the change.

## Decisions

1. **One reviewer chat replaces "Suggest improvements".** The single-shot review (`sessions/review.py`
   `review_session`, `TuningSuggestion`, `RunSuggestions.tsx`, `SuggestionApply.tsx`, `agents/reviews.py` apply flow,
   `ka.agent_session_reviews`) goes; test data only, so the table is dropped (migration).
2. **The reviewer is a run like any chat**, purpose `review`, built in code — not a saved agent row a person could edit
   or delete:
   - instructions: a fixed reviewer prompt in code (below);
   - model: the reviewed agent's model (pinned when the review starts); refused with a clear message when it has none;
   - pinned config: the reviewed session's run key (`review_of`), the reviewed agent id and its revision at start;
   - runs through the normal session worker, so run events, streaming, Stop, retries (chats: none automatic) and the
     run record work unchanged.
3. **Reviewer tools — only these** (no other catalog tools, no resources, no SSH, no experts):
   - the explorer tools (Section 1), bound to the pinned reviewed session — never another session;
   - `get_agent(…)`: the reviewed agent's current name, description, instructions (with revision), enabled tool
     names, and the list of enabled skills (name, description);
   - `search_memory`, `get_memory` (existing);
   - `read_file` on skill folders (existing skill access, to read a skill before proposing one);
   - write tools, each a normal tool call visible in the chat:
     - `update_agent_instructions(instructions, summary, revision)`: replaces the reviewed agent's instructions;
       refused when the agent's revision changed since `get_agent` (re-read and propose again); recorded in the audit
       log with old and new text and the review session; the system prompt is never reachable;
     - `create_skill(name, description, content)`: creates a skill folder with that `SKILL.md`, **disabled**; refused
       when the name exists; returns its link;
     - `save_memory` (existing, with its refuse-on-existing-name rule and revisions).
4. **Consent happens in the chat.** The reviewer prompt requires: propose first (where + what + why, the exact text),
   wait for the person's answer, and call a write tool only when the person's **latest message** agrees to that
   specific change. Enforcement is by the prompt for now; each write is visible, recorded and reversible (agent
   instructions via audit/history, a disabled skill, a memory revision). Tool-approval gates (the HITL work) can
   enforce it later.
5. **Starting a review:** "Improve agent" on a finished session — the run record page and the chat of a saved
   agent's session (chat and event purposes; not while it is working). It opens a right-side Sheet (wide) holding the
   **same chat component** the agent Chat tab uses, with a Conversations button listing earlier reviews of that
   session. The first message is the person's feedback, optionally with a turn ("about turn 3"); the reviewer's first
   step is to read the digest and the turns it needs — visible as tool calls.
6. **Where a skill goes:** created disabled under the skills root like an upload, source "created by review"; the
   person enables it on `/skills` (all agents see enabled skills).

## Reviewer prompt (gist; final text in code, tested for its key rules)

You review one finished agent session to improve that agent. Read the session with the explorer tools (start with the
digest; fetch only the turns and tool outputs you need). Decide where an improvement belongs: the agent's instructions
(how it should behave), a skill (a reusable procedure or reference many agents can use), or memory (a durable fact).
Explain where, what and why, with the exact text, and ask the person. Never change anything until the person's latest
message agrees to that specific change. If no change is justified, say so. Never put secrets in any text. The system
prompt is out of scope.

## Not in scope

Comments during a running session; automatic reviews; tool-approval gates; changing tools, resources or the system
prompt; reviewing chats with no saved agent (document/resource chats).

## Testing

Purpose and pinned config; the reviewer gets exactly its tool set; explorer tools bound to the pinned session (cannot
read another); `get_agent`; `update_agent_instructions` (revision check, audit, system prompt untouched);
`create_skill` (disabled, name clash, folder contents); start rules (finished, saved agent, model); the Sheet chat
(stream, tool calls visible, Conversations); old review code and table removed; migration up/down.
