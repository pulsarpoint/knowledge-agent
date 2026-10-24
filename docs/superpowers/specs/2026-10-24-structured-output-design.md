# Structured Output — Design

Approved direction: 2026-10-24 (conversation). A feature in its own right: an agent run can be told to finish with
JSON in a schema a person defines. First consumers (later specs): Jira tickets created deterministically from findings,
and external applications reading a run's output over REST (e.g. as a judge).

## Decisions (from the conversation)

1. **Output schemas are a shared library:** named JSON Schemas on their own page.
4. **The schema belongs to the invocation, not the agent:** the same agent runs with a schema or without one, depending
   on what the caller needs (a handler, an API start, a Test run). Agents have no default schema.
2. **Non-chat runs only:** event/handler runs, schedules (through handlers), API starts, the agent Test tab and Run
   agent. Chats stay conversational.
3. **Invalid output fails the run:** after one corrective retry, a run whose final answer does not match its schema ends
   as failed (output invalid), with the errors and the raw answer kept; nothing downstream receives it.

## 1. Output schemas (the library)

- Versioned like agent prompts (`agent_prompts` / `agent_prompt_versions`): `ka.output_schemas` (`id`, `project_id`,
  `name` unique per project, `description`, `current_version`, `revision`, timestamps) and `ka.output_schema_versions`
  (`schema_id`, `version` (1, 2, …), `schema jsonb`, `created_by`, `created_at`), migration `0136_output_schemas`. Every
  save of a changed schema adds a version; old versions are kept (runs and consumers depend on the exact shape). Down:
  drop the tables and the columns below.
- A schema is a JSON Schema in the subset strict structured output accepts: the root is an object; every object lists
  all its properties in `required` and sets `additionalProperties: false`; types `object`, `array`, `string`, `number`,
  `integer`, `boolean`, `null` (nullable via a type list), `enum`, `description`; nested objects and arrays of objects.
  A pure `check_schema(schema) -> list[str]` (problems with JSON pointers) validates it on save: anything outside the
  subset is refused with its path ("$.properties.tags: 'minItems' is not supported").
- REST: `GET/POST /api/output-schemas`, `GET/PUT/DELETE /api/output-schemas/{id}` (GET includes the version list;
  `GET /api/output-schemas/{id}/versions/{version}`) (revision; delete refused while an
  handler uses it: "Used by: <handler names>."), and `POST /api/output-schemas/check` (`{schema, sample?}` → problems,
  and whether the sample validates).
- UI: **Output schemas** page (agents area of the sidebar): list (name, description, used by handlers), editor (name,
  description, JSON editor with the check result, a sample JSON box with **Validate sample**), delete with the
  AlertDialog confirmation.

## 2. Choosing a schema for a run

- One start parameter, **`output_schema_id`** (null: a normal run). The agent is created for that run with the schema as
  its output type (§3); the same agent, saved or defined at runtime, runs with a schema or without one depending on the
  caller. Agents carry no schema and the prompt is not changed.
- Where it can be given:
  - the agent's **Test** tab (saved agents): an **Output schema** picker (none / a schema) for the next test run;
  - **Run agent** (an agent defined at runtime: the person's model and prompt): the same picker;
  - the API, `POST /api/sessions` (saved or runtime agent, non-chat purposes): `output_schema_id`, so an external
    application can call any agent with its schema;
  - event handlers that start an agent: `ka.event_handlers.output_schema_id` (nullable, → output_schemas, migration
    0136; handler editor **Output schema**: none / a schema).
- Chats (resource, document and other chat purposes) leave it null; a request that names a schema for a chat → 400
  "Chats do not take an output schema."
- A start may name a version (`output_schema_version`); without one the schema's current version is used. A handler
  can pin a version or follow the current one. The run pins the exact version (`pinned config.output_schema = {id,
  name, version, schema}`), so later edits do not change a running or finished run.

## 3. Running with a schema

- The schema reaches the model as the agent's output type, not as prompt text: the SDK sends it with every model call of
  the run as the structured-output format. The runner gives the model the schema as the run's final output type: an `AgentOutputSchemaBase` implementation over
  the JSON Schema (name, `json_schema()`, strict, `validate_json()`), in place of a pydantic type. Tools work as usual;
  the final answer must be the JSON.
- Models without structured output: a model capability **Supports structured output**
  (`capabilities.supports_structured_output`, LLM settings; on for OpenAI-style models by default). When it is off, the
  output type is still set (non-strict) and the schema is also added to the instructions as a short block ("Finish with
  JSON only, matching this JSON Schema: …") — the only case where the prompt changes. Validation and the retry below
  apply either way.
- Validation: the final answer is parsed and validated with `jsonschema` (Draft 2020-12) against the pinned schema. On
  failure the model is asked once more with the errors ("Your final answer does not match the output schema: <errors>.
  Answer again with JSON only."). Still invalid → the run ends `failed` with reason "Output did not match the schema",
  the validation errors and the raw answer recorded.
- Limits: a schema up to 64 KB; an output up to 256 KB (larger → the run fails with "Output is larger than 256 KB.").
- Valid → the run stores `output jsonb` and `output_schema_id` on `ka.agent_sessions` (migration 0136), and emits the
  event **`run.output`** (payload: run id, agent, schema id and name, the output, the started-from resource, the handler
  and event that started it) — handlers can react to it (the Jira tickets spec builds on this).
- A wake-up or resumed turn of such a run produces its output at its final turn as well (each final answer that ends the
  run is validated; intermediate turns of a multi-turn run are not — a non-chat run is one turn today; keep it simple).

## 4. Reading the output

- **Run and wait:** `POST /api/sessions?wait=<seconds>` (1–120) starts the run and holds the response until it finishes
  or the time is up. Finished → `200` with `{id, status, output}` (or the failure with its errors); time up → `202` with
  `{id, status: "running"}`, and the client follows with `/status`. Without `wait` the start answers at once, as today.
- `GET /api/sessions/{id}/status` → `{status: queued|running|waiting|finished|failed, started_at, last_activity_at,
  progress: {turns, tool_calls, step}, output_ready, error?}` — a light read for clients polling a run (`step`: the tool
  running now, or "thinking").
- `GET /api/sessions/{id}/output` → `{schema: {id, name, version}, output, valid, errors?, raw?}`; 404 when the run has no
  schema; 409 "The run has not finished." while running.
- The run page shows **Output** (formatted JSON, copy button) beside the transcript, or the validation errors and raw
  answer for a failed one. The runs list can filter "has output".
- Debug shows the pinned schema.

## Not in scope

- Delivering output to external endpoints (webhooks) — later; external apps call with `wait` or poll `/status`, then
  read `/output`.
- Inline schemas on API starts; a "Try with agent" button on the schema page — later.
- Chats with output schemas; automatic resolution of tickets; the Jira ticket action (next spec).

## Testing

- `check_schema`: accepted subset, refused keywords with paths; sample validation.
- Library API: CRUD, revision, delete refused while used, auth like other agent settings.
- Selection: handler schema, API start (saved and runtime agents), Test tab, Run agent, the same agent with and without
  a schema, chats refused, pinning.
- Runner: valid output stored and `run.output` emitted; invalid → one retry with errors → valid; still invalid → failed
  with errors and raw kept, no event.
- Versions: a changed save adds a version; runs pin the version; a handler pinned to a version keeps it.
- Model fallback: a model without structured output gets the schema block in its instructions; validation still applies.
- Limits: schema 64 KB, output 256 KB.
- Run and wait: finished within the wait (200 with output), time up (202 with id), failure; `/status` in each state.
- Output endpoint states; run page Output panel; Output schemas page (editor, check, sample, delete confirmation).
- Migration up/down.
