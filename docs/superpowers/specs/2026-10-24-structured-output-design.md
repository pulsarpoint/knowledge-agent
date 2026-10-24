# Structured Output — Design

Approved direction: 2026-10-24 (conversation). A feature in its own right: an agent run can be told to finish with
JSON in a schema a person defines. First consumers (later specs): Jira tickets created deterministically from findings,
and external applications reading a run's output over REST (e.g. as a judge).

## Decisions (from the conversation)

1. **Output schemas are a shared library:** named JSON Schemas on their own page.
4. **The schema belongs to the invocation, not the agent:** the same agent runs with a schema or without one, depending
   on what the caller needs (a handler, an API start, a Test run). Agents have no default schema.
2. **Non-chat runs only:** event/handler runs, schedules (through handlers), API starts and the agent Test tab. Chats
   stay conversational.
3. **Invalid output fails the run:** after one corrective retry, a run whose final answer does not match its schema ends
   as failed (output invalid), with the errors and the raw answer kept; nothing downstream receives it.

## 1. Output schemas (the library)

- Table `ka.output_schemas` (migration `0136_output_schemas`): `id`, `project_id`, `name` (unique per project),
  `description`, `schema jsonb`, `revision`, `created_by`, `created_at`, `updated_at`. Down: drop the table and the
  columns below.
- A schema is a JSON Schema in the subset strict structured output accepts: the root is an object; every object lists
  all its properties in `required` and sets `additionalProperties: false`; types `object`, `array`, `string`, `number`,
  `integer`, `boolean`, `null` (nullable via a type list), `enum`, `description`; nested objects and arrays of objects.
  A pure `check_schema(schema) -> list[str]` (problems with JSON pointers) validates it on save: anything outside the
  subset is refused with its path ("$.properties.tags: 'minItems' is not supported").
- REST: `GET/POST /api/output-schemas`, `GET/PUT/DELETE /api/output-schemas/{id}` (revision; delete refused while an
  handler uses it: "Used by: <handler names>."), and `POST /api/output-schemas/check` (`{schema, sample?}` → problems,
  and whether the sample validates).
- UI: **Output schemas** page (agents area of the sidebar): list (name, description, used by handlers), editor (name,
  description, JSON editor with the check result, a sample JSON box with **Validate sample**), delete with the
  AlertDialog confirmation.

## 2. Choosing a schema for a run

- The caller chooses, per invocation; agents carry no schema.
- `ka.event_handlers.output_schema_id` (nullable, → output_schemas, migration 0136): a handler that starts an agent runs
  it with that schema, or without one (handler editor: **Output schema**: none / a schema).
- API start (`POST /api/sessions` with a saved agent, non-chat purposes): optional `output_schema_id`.
- The agent's **Test** tab: an **Output schema** picker (none / a schema) for the next test run, so the same agent can
  be tried both ways.
- Chats (resource, document, ad-hoc chat purposes) never get an output schema; a request that names one for a chat →
  400 "Chats do not take an output schema."
- The run pins the schema (`pinned config.output_schema = {id, name, schema}`), so later edits of the schema do not
  change a running or finished run.

## 3. Running with a schema

- The runner gives the model the schema as the run's final output type: an `AgentOutputSchemaBase` implementation over
  the JSON Schema (name, `json_schema()`, strict, `validate_json()`), in place of a pydantic type. Tools work as usual;
  the final answer must be the JSON.
- Validation: the final answer is parsed and validated with `jsonschema` (Draft 2020-12) against the pinned schema. On
  failure the model is asked once more with the errors ("Your final answer does not match the output schema: <errors>.
  Answer again with JSON only."). Still invalid → the run ends `failed` with reason "Output did not match the schema",
  the validation errors and the raw answer recorded.
- Valid → the run stores `output jsonb` and `output_schema_id` on `ka.agent_sessions` (migration 0136), and emits the
  event **`run.output`** (payload: run id, agent, schema id and name, the output, the started-from resource, the handler
  and event that started it) — handlers can react to it (the Jira tickets spec builds on this).
- A wake-up or resumed turn of such a run produces its output at its final turn as well (each final answer that ends the
  run is validated; intermediate turns of a multi-turn run are not — a non-chat run is one turn today; keep it simple).

## 4. Reading the output

- `GET /api/sessions/{id}/output` → `{schema: {id, name}, output, valid, errors?, raw?}`; 404 when the run has no
  schema; 409 "The run has not finished." while running.
- The run page shows **Output** (formatted JSON, copy button) beside the transcript, or the validation errors and raw
  answer for a failed one. The runs list can filter "has output".
- Debug shows the pinned schema.

## Not in scope

- Delivering output to external endpoints (webhooks) — later; external apps read `GET /api/sessions/{id}/output`.
- Chats with output schemas; automatic resolution of tickets; the Jira ticket action (next spec).

## Testing

- `check_schema`: accepted subset, refused keywords with paths; sample validation.
- Library API: CRUD, revision, delete refused while used, auth like other agent settings.
- Selection: handler schema, API start, Test tab, the same agent with and without a schema, chats refused, pinning.
- Runner: valid output stored and `run.output` emitted; invalid → one retry with errors → valid; still invalid → failed
  with errors and raw kept, no event.
- Output endpoint states; run page Output panel; Output schemas page (editor, check, sample, delete confirmation).
- Migration up/down.
