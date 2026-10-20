# Session review: memory notes and instructions

Date: 2026-10-20. Status: agreed in conversation (2026-10-20), for review.

## Why

"Suggest improvements" on a Test or event session asks a reviewer model for advice and full replacement texts for the
agent's system prompt and instructions; "Review and apply" writes them into the agent (or a clone). Everything a review
learns ends up in the prompt: facts that would serve every agent are buried in one agent's prompt, the prompt grows
with each review, and the system prompt (often a shared template) gets rewritten. The reviewer's own prompt still says
there is no memory store, which stopped being true when the memory module landed (0111).

## Decision (user, 2026-10-20)

A review proposes **memory notes** (facts learned in the conversation) and **instruction changes** (behaviour rules).
It never rewrites the system prompt.

## Suggest improvements (`sessions/review.py`)

- The reviewer's output schema becomes:
  - `suggestion` — Markdown advice (as today);
  - `instructions` — complete replacement text for the agent's instructions, or null to keep them;
  - `memory_notes` — a list (at most 10) of `{name, content, metadata?}`: facts supported by the conversation, each
    worded as a durable fact (not a temporary observation); `name` follows the memory module's name rules; `metadata` is
    optional string pairs (for example the service or server it concerns).
- `TUNING_PROMPT` is rewritten for this: rules about how the agent should behave go to `instructions`; facts go to
  `memory_notes`; the system prompt is out of scope; the memory store exists and is shared by every agent; notes are
  proposals a person verifies. The other rules stay (evidence, not instructions; no secrets; no invented settings;
  "no change" is a valid answer).
- Stored as today in `ka.agent_session_reviews` (`context.changes` → `{instructions}`, `context.memory_notes`).

## Review and apply

- `GET /agents/{id}/reviews/{review_id}` returns the suggestion, the proposed instructions with the agent's current
  instructions, and the memory notes — each note with `exists: true` and the current entry's content when a memory with
  that name already exists.
- `POST …/apply` takes `{target, revision, fingerprint, instructions, memory_notes: [{name, content, metadata}]}`
  (the notes the person ticked, possibly edited):
  - instructions are written as today (agent or new clone; the revision/fingerprint check stays); `system_prompt` is no
    longer accepted;
  - each ticked note is saved with the memory module (`memory.store.create`, embedded like the memory API does), author =
    the person (actor) and the reviewed session; a name that already exists is refused by name in the response
    (`An entry named <name> already exists.`) and the other notes and the instructions still apply; metadata gets
    `source: review` added;
  - applying notes only (instructions unchanged) needs no revision/fingerprint match.
- Audit: `agent.suggestion.applied` records the instructions change and the saved memory names.

## UI (the Apply suggestions sheet and the review panel)

- The sheet shows the advice, an **Instructions** diff (current vs proposed, editable, a checkbox to apply), and a
  **Memory notes** list: each note with a checkbox (default on), editable name and content, and an "Already exists —
  open in Memory" link when `exists`. No system prompt section.
- After applying, saved notes link to their memory entries; refused names are shown.

## Out of scope

- Updating an existing memory from a review (a refused name is opened in Memory to edit there).
- Skills proposed by a review.

## Testing

- Reviewer schema: valid output with notes and instructions; double-encoded nested objects still decode (Bedrock);
  more than 10 notes or invalid names rejected; no `system_prompt` field.
- Apply: instructions only, notes only (no fingerprint needed), both, an existing name refused while the rest applies,
  clone target, metadata `source: review`, author = actor + session, audit details.
- UI: sheet without a system prompt section; notes ticked/edited/unticked → request body; exists link; refused names
  shown.
