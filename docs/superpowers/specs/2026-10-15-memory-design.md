# Memory: a shared store agents save to and search

Date: 2026-10-15. Status: for review.
Builds on: the tool catalog (`@tool` + `tools/tools.json` + `ka.tools`, 0108), events (0088–0091), embedding models
(0037, `ka.rag_workspaces.embedding_model_id`), RAG hybrid search (`services/rag/src/rag/workspace_search.py`).

## Why

Agents learn things in one run (how a service deploys, what fixed an incident, who owns a queue) and forget them by
the next. A memory every agent can save to and search lets that knowledge carry across runs and across agents, and
people can read and correct it.

Decided with the person using it:

1. **Shared, tagged by agent.** One memory per project; every entry records which agent saved it; search sees all
   entries and can filter to one agent.
2. **An entry** is a unique **name**, a **metadata** dict (string → string) for filtering, and **content** searched by
   meaning (embedding) and by words (full text).
3. **No silent overwrite.** Saving an existing name is refused and returns the existing entry; the agent merges and
   calls update with the revision it read. Every change keeps the earlier version (restorable). Saves and updates
   record events, so a curator agent can be added later as an event handler — not built now.
4. **Lives in admin-api**, Postgres tables; the embedding model is the one chosen for RAG; the embedding code moves
   to the shared `knowledge_llm` package.
5. **People** see, search, create, edit, restore and delete entries in Administration → Memory. Agents cannot delete.

## Storage (migration 0111)

```sql
CREATE TABLE ka.memories (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         uuid NOT NULL REFERENCES ka.projects(id),
    name               text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9._/-]{0,199}$'),
    metadata           jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
    content            text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 20000),
    search_vector      tsvector GENERATED ALWAYS AS (to_tsvector('simple',
                           replace(name, '/', ' ') || ' ' || content || ' ' || ka.jsonb_values_text(metadata))) STORED,
    embedding          public.vector,
    embedding_profile  text,
    revision           integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_by_agent   text REFERENCES ka.agents(id) ON DELETE SET NULL,
    updated_by_agent   text REFERENCES ka.agents(id) ON DELETE SET NULL,
    created_by_session uuid,
    updated_by_session uuid,
    created_by_actor   uuid REFERENCES ka.principals(id),
    updated_by_actor   uuid REFERENCES ka.principals(id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name),
    CHECK ((embedding IS NULL) = (embedding_profile IS NULL))
);
CREATE INDEX memories_search ON ka.memories USING gin(search_vector);
CREATE INDEX memories_metadata ON ka.memories USING gin(metadata jsonb_path_ops);

CREATE TABLE ka.memory_versions (
    memory_id   uuid NOT NULL REFERENCES ka.memories(id) ON DELETE CASCADE,
    revision    integer NOT NULL,
    name        text NOT NULL,
    metadata    jsonb NOT NULL,
    content     text NOT NULL,
    by_agent    text,
    by_session  uuid,
    by_actor    uuid,
    changed_at  timestamptz NOT NULL,
    PRIMARY KEY (memory_id, revision)
);
```

- `ka.jsonb_values_text(jsonb)` is a new `IMMUTABLE` SQL function (the metadata values joined by spaces) so a generated
  column can use it.
- The actor columns are `ka.principals` ids (`store.actor`, the signed-in person), the session columns hold
  `ka.agent_sessions.id` (no foreign key: sessions may be deleted, the trace stays).
- **Metadata rules** (checked in code, one message per rule): at most 20 keys; keys `^[a-z][a-z0-9_]{0,63}$`; values
  strings of 1–500 characters.
- `memory_versions` holds every **earlier** revision: before an update, restore or delete, the current row is copied
  there. Deleting an entry deletes its history (people only, confirmed).
- No vector index for now (memory is small; an exact scan). No dimension on `embedding`: a profile change re-embeds.
- Migration 0111 also adds `'memory'` to `ka.tools.tool_group` and to `events.source` (`events_source_check`), and
  down reverses all of it (refusing when `memory` tools or events exist is not needed: test data).

## Embedding

- `embed(model, texts, transport)` and `embedding_profile(model)` move from `services/rag/src/rag/workspace_models.py`
  to `packages/llm-settings/src/knowledge_llm/embeddings.py`, raising `ModelUnavailable` instead of `HTTPException`;
  RAG keeps thin wrappers that map it to 502 as today. No behaviour change for RAG.
- Memory uses the project's RAG embedding model (`ka.rag_workspaces.embedding_model_id`, enabled, decoded with
  `knowledge_llm.decode_model` and `CREDENTIAL_ENCRYPTION_KEY`, which admin-api and agent-session-worker both have).
- On save, update and restore the content is embedded **before** the transaction (one short HTTP call, no lock held).
  When there is no model, it is disabled, or the call fails, the entry is saved with `embedding = NULL`; the tool
  reply and the UI say *not embedded — found by words only*.
- The stored vector is used only while `embedding_profile` equals the current model's profile; others count as not
  embedded. **Re-embed** embeds those entries in batches of 32.

## Search

`memory/search.py` `search(conn, project_id, query, metadata, agent_id, limit, vector) -> list[dict]`:

- Filters: `metadata @> %s` (all given pairs must match) and `updated_by_agent = %s OR created_by_agent = %s` when an
  agent is given.
- With a query: two rankings of up to `limit * 3` rows — full text (`search_vector @@ websearch_to_tsquery('simple',
  q)`, `ts_rank_cd`) and, when the query was embedded with the current profile, vector distance
  (`embedding <=> q` among rows with that profile) — merged by reciprocal rank (`1 / (60 + position + 1)`, summed), as
  RAG does; ties by `updated_at DESC, id`.
- Without a query: the filtered entries, newest `updated_at` first.
- `limit` 1–50 (tool default 10, UI pages of 25 with an offset).

## Agent tools

New group **Memory** in `tools/tools.json` (`needs: null` — available to any agent; turned on per agent in its Tools
tab). Functions in `capabilities/memory.py`, `@tool`-decorated like the others. The run's agent is the session's
(`config['session_id']` → `ka.agent_sessions.agent_id`); a run without a session (agent Test) saves with no session
and no agent.

| tool | arguments | reply |
|---|---|---|
| `search_memory` | `query?`, `metadata?` (dict), `agent?` (agent name), `limit=10` | matches: name, metadata, saved by (agent name or *a person*), updated, revision, embedded or not, content cut at 2,000 characters with *… (call get_memory for the full entry)* |
| `get_memory` | `name` | the entry in full, with its revision |
| `save_memory` | `name`, `content`, `metadata?` | created (name, revision 1, embedded or not); if the name exists: **refused**, with the existing entry and its revision and *An entry with this name exists. Merge your new information into its content and call update_memory with revision N.* |
| `update_memory` | `name`, `content`, `metadata`, `revision` | updated (new revision); if `revision` is not current: **refused**, with the current entry and *It changed since you read it: merge again and call update_memory with revision N.*; unknown name: *No entry named X; use save_memory.* |

Validation errors (name, metadata, content length, unknown agent name) come back as the tool's text with the rule
broken, so the agent can correct the call. Usage texts (the docstrings) tell agents: search before saving; one fact
per entry under a stable name; use metadata for what you will filter by (e.g. `service`, `kind`); update rather than
duplicate.

## Events

Each create, update, restore and delete records an event in the same transaction: `memory.saved`,
`memory.updated`, `memory.restored`, `memory.deleted`, `source = 'memory'`, `source_id` = the memory id, `subject` =
the name, payload `{memory_id, name, metadata, revision, agent_id, session_id, actor}` (no content: handlers read it
with `get_memory`). Handlers can react to them like any event; none is added now.

## API

`memory/api.py`, admin gateway and `docs-admins`, like the other Administration routes:

- `GET /api/memories?query=&metadata=<json>&agent=<id>&offset=&limit=` — search/list (search above), plus `total`
  for the filters and the count of entries not embedded with the current model.
- `GET /api/memories/{id}` — the entry and its versions (newest first).
- `POST /api/memories` `{name, metadata, content}` — 201; an existing name → 409 with the existing entry's id.
- `PUT /api/memories/{id}` `{name, metadata, content, revision}` — renaming is allowed (unique); a stale revision →
  409 *This entry changed since you opened it.* with the current entry.
- `DELETE /api/memories/{id}?revision=N` — 204; stale → 409.
- `POST /api/memories/{id}/restore` `{revision, current_revision}` — makes that version the new current revision
  (the current one goes to history first); stale → 409.
- `POST /api/memories/re-embed` — embeds every entry not embedded with the current model; returns
  `{embedded, failed}`; no model → 409 *Select an embedding model in RAG Settings.*

## UI

Administration → **Memory** (`MemoryView.tsx`, route in the admin navigation next to Events):

- **List**: search box, metadata filter chips (`key = value`, added from a small form or by clicking a badge), agent
  filter; rows show name, metadata badges, saved by (agent or person), updated, revision, a *Not embedded* badge;
  pages of 25. A **Re-embed** button appears when some entries are not embedded.
- **Entry sheet** (new or opened): name, metadata as key/value rows, content textarea, Save (sends the revision; a
  409 shows *This entry changed since you opened it* with a button to load the current one). **History** tab:
  earlier versions, newest first, each with who/when, a diff against the current content and **Restore**. **Delete**
  asks for confirmation.

## Error handling

- Embedding unavailable or failing: saved without a vector, reported; never fails a save.
- Database unavailable: the tool reply is *Memory is unavailable right now.*; the API returns 503 as elsewhere.
- Concurrent saves of the same new name: the unique constraint makes one fail; it gets the *exists* reply.

## Testing

- Migration 0111 up/down; the CHECKs (name pattern, content length, embedding/profile pair); `jsonb_values_text`.
- `knowledge_llm.embeddings`: the moved functions with the RAG tests still passing unchanged.
- Store: create; existing name refused with the entry; update with current and stale revision; history rows written;
  restore makes a new revision; delete removes history; events recorded with the payload above.
- Search: words; meaning (a stub embeddings transport with fixed vectors); metadata containment (all pairs);
  agent filter; reciprocal-rank merge order; no vectors → full text only; a vector from another profile ignored;
  no query → newest first.
- Embedding: no model → saved, not embedded; failing call → saved, not embedded; re-embed after a profile change.
- Tools: each reply shape, the 2,000-character cut, validation messages, agent and session recorded, Test run with no
  session; catalog registration (golden tool schemas updated by the four new tools only).
- API: each route, 409s, gateway required.
- UI: list and filters, create, edit with a 409, history diff and restore, delete confirmation, re-embed button.
- Full backend and admin-ui suites.

## Docs

`docs/user-guide.md` (Memory page, the four tools), `docs/migrations.md` (0111), `docs/database.md` (the two tables).

## Out of scope

- A curator agent (later: a handler on `memory.*` events).
- Private or per-agent memory; expiry or forgetting; vector indexes; import/export.
- Memory in prompts automatically (agents search when they need it).
