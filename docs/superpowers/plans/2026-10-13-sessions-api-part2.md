# One Sessions API, Part 2 (Saved Agents and the Test Tab) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Saved agents start through `POST /api/sessions` with `agent_id` (narrow-only), and the agent's Test tab uses the same chat component and settings sheet as Ask agent.

**Architecture:** `sessions/start.py` gains `narrow()`, which pins a saved agent's configuration and applies only reductions; `build()` dispatches to it when `agent_id` is given. Options, the list and the tuning review join `/api/sessions`; the `/api/agents/{id}/sessions…` routes go. On the UI, `AgentPane` becomes `AgentChat` with a scope (a document or a saved agent); the Test tab renders it, with the feedback and suggestions UI extracted into `RunSuggestions`.

**Tech Stack:** FastAPI + psycopg 3 (pytest with a throwaway Postgres), React Router 7 + shadcn + vitest (pnpm).

**Spec:** `docs/superpowers/specs/2026-10-13-sessions-api-part2-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/sessions-part2` (branch `feature/sessions-part2`). Never run `make` targets, never touch `.env` or `.local`, never `git stash`, never touch the running Docker stack or its database.
- Backend tests: `cd services/admin-api && uv run --frozen pytest -q <files>`. Known failures to ignore: code-graph tests in worktrees (no `services/code-graph/node_modules`), `test_deploy::test_docs_commands_serve_the_docs_folder_inside_the_data_root`, `test_environments::test_migration_reapply_and_populated_rollback_protection`.
- UI checks: `cd services/admin-ui && pnpm exec vitest run`, `pnpm exec tsc --noEmit`, `pnpm exec react-router routes`. Once, from the worktree root: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` (git-ignored; never commit it; never `pnpm install`).
- Refusal texts, verbatim: `{tool} is not one of this agent's tools.`; `This agent does not allow subagents.`; `A saved agent uses its own model and prompt.`; `A saved agent takes no environments; choose its servers or services.`; `A saved agent takes no documents yet.`; experts `This expert is not available: {id}.`; resources: `run_resources.resolve`'s own messages (`This agent uses fixed resources.`, `This agent does not use {what}.`, …).
- UI copy, verbatim: `Change them in General.`; `This agent's resources are fixed.`; the Test tab's cannot-start reasons stay as today (`This agent is disabled. Enable it in General to run a task.`, `Loading agent configuration…`, `Save or discard changes in the configuration tabs before starting a task.`).
- Behaviour comes from the prompt: code appends only the start's `context` under `# Context`.
- Commit after each task; messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Never `git add` `.superpowers` or `node_modules`.

## Review Focus

1. A request with `agent_id` that tries to add anything (a tool the agent has off, another agent's expert, subagents the agent does not allow, a resource outside a per-run agent's selection, any resource for a fixed agent, another model or prompt) must be refused — Task 1 pins each case.
2. Event handlers still start runs after `start_session` is rebuilt on `narrow` — Task 2 keeps the event-session tests green.
3. Browser settings saved for an agent before it changed (another revision) must not narrow a new run — Task 3 pins the drop.
4. The Ask agent pane must behave exactly as before after the rename to `AgentChat` — Task 5 keeps every existing pane test.
5. A Test run's Add feedback → Suggest improvements → Review and apply flow must work from the shared chat — Task 5 pins it.

---

### Task 1: `narrow()` — start and preview a saved agent

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/sessions/start.py` (`StartInput`, `build`, new `narrow`)
- Modify: `services/admin-api/src/knowledge_admin/sessions/api.py` (the start route's insert for a saved agent)
- Test: `services/admin-api/tests/test_sessions_saved_agent.py` (create)

**Interfaces:**
- Produces: `narrow(conn, project, agent_id: str, value: StartInput) -> tuple[dict, dict]` (pinned config, model); `build(conn, project, value, session_id)` returns `(pinned, model, target)` where `target` is `None` for a saved agent; constants `NOT_ITS_TOOL = "{tool} is not one of this agent's tools."`, `NO_SUBAGENTS = 'This agent does not allow subagents.'`, `OWN_MODEL = 'A saved agent uses its own model and prompt.'`, `NO_ENVIRONMENTS = 'A saved agent takes no environments; choose its servers or services.'`, `NO_DOCUMENTS = 'A saved agent takes no documents yet.'`. `NO_SAVED_AGENTS` is deleted.
- `StartInput`: `started_from: StartedFrom | None = None`, `model_id: UUID | None = None`, `system_prompt: str = ''`; the validator requires `started_from`, `model_id` and a non-empty `system_prompt` only when `agent_id` is None (messages `Choose what the agent starts from.`, `Choose a model.`, `Enter a system prompt`).

- [ ] **Step 1: Write the failing tests**

Look at `tests/test_test_sessions.py` for how it creates a saved agent with tools, experts, per-run resources and a model (its fixtures), and reuse those fixtures (import them) rather than building new ones. The tests to write:

```python
"""Starting a saved agent through /api/sessions: its configuration is the ceiling; the request may only narrow it."""
from test_repositories import HEADERS

# `agent`: a saved, enabled agent with a model, tools ssh_run and read_file on, run_subagent on, one expert offered,
# per-run resources with server web-1 and service Payments in its selection; `fixed`: the same agent with fixed
# resources. Build them with the fixtures test_test_sessions.py uses.


def start(client, path='', **body):
    return client.post(f'/api/sessions{path}', headers=HEADERS, json={'message': 'Check the disk.', **body})


def test_a_saved_agent_starts_with_its_own_configuration_narrowed(agent):
    for path in ('/preview', ''):
        response = start(agent.client, path, agent_id=agent.id, tools=['read_file'], expert_ids=[],
                         subagents=False, resources={'server_ids': [agent.web]})
        assert response.status_code in (200, 201), response.text
    session = response.json()
    assert (session['purpose'], session['agent_id'], session['target_document_id']) == ('test', agent.id, None)
    modes = {item['tool']: item['mode'] for item in session['config']['tools']['items']}
    assert modes['read_file'] == 'on' and modes['ssh_run'] == 'off' and modes['run_subagent'] == 'off'
    assert session['config']['experts'] == []
    assert session['run_resources']['server_ids'] == [agent.web]
    preview = start(agent.client, '/preview', agent_id=agent.id, tools=['read_file'], resources={'server_ids': [agent.web]}).json()
    assert preview['config']['system_prompt'] == session['config']['system_prompt']


def test_nothing_can_be_added_to_a_saved_agent(agent, fixed):
    other_expert = '00000000-0000-0000-0000-000000000009'
    for body, message in (
            ({'tools': ['get_service_info']}, "get_service_info is not one of this agent's tools."),
            ({'expert_ids': [other_expert]}, f'This expert is not available: {other_expert}.'),
            ({'subagents': True, 'agent_id': agent.no_subagents}, 'This agent does not allow subagents.'),
            ({'model_id': agent.other_model}, 'A saved agent uses its own model and prompt.'),
            ({'system_prompt': 'Be someone else.'}, 'A saved agent uses its own model and prompt.'),
            ({'resources': {'environment_ids': [agent.environment]}}, 'A saved agent takes no environments; choose its servers or services.'),
            ({'resources': {'documents': [{'id': agent.document, 'editable': True}]}}, 'A saved agent takes no documents yet.'),
            ({'resources': {'server_ids': [agent.outside_server]}}, None)):
        for path in ('/preview', ''):
            response = start(agent.client, path, **{'agent_id': agent.id, **body})
            assert response.status_code == 400, (body, response.text)
            if message:
                assert response.json()['error']['message'] == message
    refused = start(fixed.client, agent_id=fixed.id, resources={'server_ids': [fixed.web]})
    assert refused.status_code == 400 and refused.json()['error']['message'] == 'This agent uses fixed resources.'


def test_the_agents_own_model_and_prompt_may_be_sent_back(agent):
    response = start(agent.client, '/preview', agent_id=agent.id, model_id=agent.model, tools=['read_file'],
                     resources={'server_ids': [agent.web]})
    assert response.status_code == 200, response.text


def test_narrowing_away_what_a_tool_needs_is_refused(agent):
    response = start(agent.client, agent_id=agent.id, tools=['ssh_run'], resources={})
    assert response.status_code == 400 and response.json()['error']['message'] == 'ssh_run needs at least one server.'


def test_context_is_added_as_facts(agent):
    pinned = start(agent.client, '/preview', agent_id=agent.id, tools=['read_file'], context='Ticket OPS-1',
                   resources={'server_ids': [agent.web]}).json()['config']
    assert pinned['system_prompt']['content'].endswith('# Context\n\nTicket OPS-1')
```

Give the fixture namespace the fields the tests use (`client`, `id`, `web`, `model`, `other_model`, `environment`, `document` (a saved document's id), `outside_server`, `no_subagents` — a second agent with `run_subagent` off). A `system_prompt` equal to the agent's own text is allowed; any other non-empty text is refused.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_sessions_saved_agent.py`
Expected: FAIL (`Saved agents start from their Test tab for now.`).

- [ ] **Step 3: Implement `narrow()`**

In `sessions/start.py` (imports: `get_agent_config`, `per_run`, `require_run_resources`, `model_fits` from `knowledge_admin.knowledge_agents.config`; `RunResources`, `resolve` from `knowledge_admin.knowledge_agents.run_resources`; `pin` from `knowledge_admin.knowledge_agents.experts`):

```python
def narrow(conn, project: UUID, agent_id: str, value: StartInput, conninfo: str) -> tuple[dict, dict]:
    """A saved agent's configuration for one run, narrowed by the request and never widened: its tools that are on
    (switch off only), a subset of its experts, subagents only if it allows them, and for a per-run agent a subset of
    its saved resources. Its model and system prompt are its own."""
    try:
        config = get_agent_config(agent_id, conninfo=conninfo)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    model = config['model']
    if (value.model_id is not None and (model is None or str(value.model_id) != str(model['id']))) \
            or (value.system_prompt and value.system_prompt != config['system_prompt']['content']) \
            or value.prompt_id is not None:
        raise HTTPException(400, OWN_MODEL)
    if value.resources.environment_ids:
        raise HTTPException(400, NO_ENVIRONMENTS)
    if value.resources.documents:
        raise HTTPException(400, NO_DOCUMENTS)
    resources = value.resources
    requested = RunResources(code_project_ids=resources.code_project_ids, collection_ids=resources.collection_ids,
                             server_ids=resources.server_ids, service_ids=resources.service_ids) \
        if any((resources.code_project_ids, resources.collection_ids, resources.server_ids, resources.service_ids)) or per_run(config) \
        else None
    try:
        pinned = resolve(conn, project, config, requested)
        require_run_resources(pinned)
        if not pinned['enabled']:
            raise ValueError('Agent is disabled.')
        if model is None or not model['enabled'] or not model_fits(model):
            raise ValueError(AGENT_NO_MODEL)
    except RunResourceError as exc:
        raise HTTPException(400, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    pinned = pin(conn, pinned, agent_id)
    items = {item['tool']: item for item in pinned['tools']['items']}
    on = {name for name, item in items.items() if item['mode'] == 'on'}
    added = [name for name in value.tools if name not in on]
    if added:
        raise HTTPException(400, ' '.join(NOT_ITS_TOOL.format(tool=name) for name in added))
    if value.subagents and SUBAGENT not in on:
        raise HTTPException(400, NO_SUBAGENTS)
    for name, item in items.items():
        if name == SUBAGENT:
            item['mode'] = 'on' if value.subagents else 'off'
        elif name in CATALOG and name in on and name not in value.tools:
            item['mode'] = 'off'
    names = {spec.name: spec for spec in CATALOG.values()}
    unmet = [NEEDS.format(tool=name, what=REQUIRES[names[name].needs]) for name in value.tools
             if name in names and names[name].needs in REQUIRES and not needs_met(names[name].needs, pinned, None)]
    if unmet:
        raise HTTPException(400, ' '.join(unmet))
    experts = {entry['id']: entry for entry in pinned['experts']}
    for expert_id in value.expert_ids:
        if str(expert_id) not in experts:
            raise HTTPException(400, NO_EXPERT.format(id=expert_id))
    pinned['experts'] = [experts[str(expert_id)] for expert_id in value.expert_ids]
    if value.context:
        content = pinned['system_prompt']['content'] + f'\n\n# Context\n\n{value.context}'
        pinned['system_prompt'] = {**pinned['system_prompt'], 'content': content}
    pinned['project_id'] = str(project)
    return jsonable_encoder(pinned), model
```

with `AGENT_NO_MODEL = 'Select an enabled model in this agent’s General tab.'` (the Test tab's text, moved from `agent_sessions.py`). `tools` empty means "all of the agent's tools off": the UI always sends the tools it keeps; the start does not guess. In `build()`, replace the `NO_SAVED_AGENTS` refusal with:

```python
    if value.agent_id is not None:
        pinned, model = narrow(conn, project, value.agent_id, value, conninfo)
        return pinned, model, None
```

and give `build` a `conninfo: str` parameter (callers pass `db.conninfo`). The `StartInput` validator becomes: strip and null-check `message`, `system_prompt`, `context`; `message` required; when `agent_id is None` require `started_from` (`Choose what the agent starts from.`), `model_id` (`Choose a model.`) and `system_prompt` (`Enter a system prompt`).

- [ ] **Step 4: Insert a saved agent's run in the start route**

In `sessions/api.py` `start_session`, when `value.agent_id` is set, insert with `agent_id=value.agent_id`, purpose `'test'`, `target_document_id` NULL, `prompt_snapshot={'system_prompt': agent_system_prompt(pinned), 'context': value.context}` (`agent_system_prompt` from `knowledge_admin.session_text`), `context_snapshot={'folders': [], 'code_project_ids': []}`, `run_resources=pinned['run_resources'] if per_run(pinned) else None`; audit `agent_run.created` with `{'agent_id', 'model_id', 'tools_off', 'expert_ids', 'subagents', 'resources': pinned.get('run_resources')}`. The ad-hoc branch stays as it is. Title: the message's first line, as now.

- [ ] **Step 5: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_sessions_saved_agent.py tests/test_sessions_start.py tests/test_sessions_api.py`
Expected: PASS (update `test_sessions_start.py`: its `agent_id` refusal test goes — a saved agent now starts — while the `started_from` refusal for ad-hoc runs stays; its test that monkeypatches `start.build` must follow the new `build(conn, project, value, session_id, conninfo)` signature).

- [ ] **Step 6: Commit**

```bash
git add -A services/admin-api
git commit -m "feat(agents): saved agents start through /api/sessions and can only be narrowed"
```

---

### Task 2: Options, list and review for saved agents; the old Test routes go

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/sessions/start.py` (`options` for an agent)
- Modify: `services/admin-api/src/knowledge_admin/sessions/api.py` (`options` query `agent_id`, list `agent_id`, `POST /{id}/review`)
- Modify: `services/admin-api/src/knowledge_admin/agent_sessions.py` (remove the router; `start_session` on `narrow`; keep `conversation`, `ReviewInput`, the reviewer code as a function `review_session(...)`)
- Modify: `services/admin-api/src/knowledge_admin/api.py` (stop including `agent_sessions`' router)
- Modify tests: `tests/test_test_sessions.py`, `tests/test_test_session_feedback.py`, `tests/test_test_session_worker.py`, `tests/test_event_sessions.py` and any other file calling `/api/agents/{id}/sessions`

**Interfaces:**
- Produces: `start.agent_options(conn, project, agent_id, conninfo) -> dict`: `{agent: {id, name, revision, model: {id, name} | None, system_prompt, resources_fixed, subagents_allowed}, models: [agent model], default_model_id, prompts: [], default_prompt_id: None, resources: {servers, services, environments: [], code_projects, collections, documents: []}, tools, experts}`. `resources` lists, for a per-run agent, its usable saved selection (`run_resources.options`, mapped to `{id, name, linked: False, usable: True, reason: None}` for servers and `{id, name, linked: False, brings_code: False, brings_servers: False}` for services); for a fixed agent every list is empty and `resources_fixed` is true. `tools`: the agent's tools that are on (catalog entries with `name, label, summary, group, needs, requires`), `run_subagent` excluded. `experts`: the agent's offered experts `{id, name, when_to_use}`.
- `GET /api/sessions/options?agent_id=…` (404 for an unknown agent); `GET /api/sessions?agent_id=…` (purpose `test` or `event`, newest first, 100); `POST /api/sessions/{id}/review` with `{comment, turn?}` (today's `ReviewInput` and behaviour; 404 `Session not found.` for another project's run; 400 `Suggestions are for Test and event sessions only.` for other purposes).
- `start_session(conn, project, agent_id, actor, message, *, purpose='test', run_resources=None, title=None, conninfo='')` keeps its signature and returns a session id; internally it builds a `StartInput(agent_id=…, message=…, tools=<the agent's tools that are on>, subagents=<allowed>, expert_ids=<all offered>, resources=<run_resources>)` and calls `narrow`, so an event run pins exactly what it pinned before. Its `SessionRefused` keeps the same statuses (map `HTTPException` to `SessionRefused(exc.status_code, exc.detail)`).

- [ ] **Step 1: Write the failing tests**

Move every test in `tests/test_test_sessions.py` and `tests/test_test_session_feedback.py` that calls `POST/GET /api/agents/{id}/sessions` or `…/review` onto the new routes: creating becomes `POST /api/sessions` with `{'agent_id': id, 'message': …, 'tools': <the agent's on tools>, 'subagents': <its switch>, 'expert_ids': <all its experts>, 'resources': <run_resources or {}>}` (add a helper `test_start(client, agent_id, message, run_resources=None)` in the test module that reads `/api/sessions/options?agent_id=` and sends everything the agent has on); listing becomes `GET /api/sessions?agent_id=…`; review becomes `POST /api/sessions/{sid}/review`. Add:

```python
def test_options_for_a_saved_agent_offer_only_what_may_be_narrowed(agent, fixed):
    options = agent.client.get(f'/api/sessions/options?agent_id={agent.id}', headers=HEADERS).json()
    assert options['agent']['id'] == agent.id and not options['agent']['resources_fixed']
    assert options['agent']['subagents_allowed'] and options['prompts'] == [] and options['default_prompt_id'] is None
    assert {tool['name'] for tool in options['tools']} == {'ssh_run', 'read_file'}
    assert [server['id'] for server in options['resources']['servers']] == [agent.web]
    fixed_options = fixed.client.get(f'/api/sessions/options?agent_id={fixed.id}', headers=HEADERS).json()
    assert fixed_options['agent']['resources_fixed'] and fixed_options['resources']['servers'] == []
    assert agent.client.get('/api/sessions/options?agent_id=nope', headers=HEADERS).status_code == 404


def test_the_old_test_routes_are_gone(agent):
    some = '00000000-0000-0000-0000-000000000001'
    for method, path in (('post', f'/api/agents/{agent.id}/sessions'), ('get', f'/api/agents/{agent.id}/sessions'),
                         ('post', f'/api/agents/{agent.id}/sessions/{some}/review')):
        assert getattr(agent.client, method)(path, headers=HEADERS, **({'json': {}} if method == 'post' else {})).status_code in (404, 405)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_test_sessions.py tests/test_test_session_feedback.py`
Expected: FAIL (404 on `/api/sessions/options?agent_id=`, old routes still answer).

- [ ] **Step 3: Implement**

- `start.agent_options` as in **Interfaces**, reusing `run_resources.options(conn, project, config)` for a per-run agent's lists, `experts.offered(conn, agent_id)` for experts and the catalog for tool labels (`REQUIRES` for `requires`).
- `sessions/api.py`: `options` takes `agent_id: str | None = Query(None)` and returns `start.agent_options(...)` when given (a `document_id` together with `agent_id` is a 400 `Choose a document or an agent.`); the list takes `agent_id: str | None` and filters `s.agent_id=%s AND s.purpose IN ('test','event')`, newest first; `POST /{session_id}/review` calls `agent_sessions.review_session(db, project, session_id, value, request_actor(request))` — move the body of today's `review` route into that function unchanged, replacing its `agent_id` filter with the session's own `agent_id` (the route no longer has one) and keeping every message.
- `agent_sessions.py`: delete `build_api`, `TestSessionInput` and the routes; rebuild `start_session` as in **Interfaces**; keep `conversation`, `ReviewInput`, `TUNING_PROMPT`, the constants. Update its module docstring ("the saved-agent start used by event handlers, and the tuning review").
- `api.py`: remove `agent_sessions.build_api` from the router list.

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_test_sessions.py tests/test_test_session_feedback.py tests/test_test_session_worker.py tests/test_event_sessions.py tests/test_sessions_saved_agent.py tests/test_sessions_api.py` then the full suite once.
Expected: PASS apart from the known failures.

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-api
git commit -m "feat(agents): options, list and review for saved agents at /api/sessions; the old Test routes go"
```

---

### Task 3: The client — saved-agent options and settings per scope

**Files:**
- Modify: `services/admin-ui/app/lib/sessions.ts`, `services/admin-ui/app/lib/agentSettings.ts`
- Delete: `services/admin-ui/app/lib/testSessions.ts` (move `SessionReview` into `sessions.ts`; update `SuggestionApply.tsx` and other importers)
- Test: `services/admin-ui/app/lib/__tests__/agentSettings.test.ts`, `lib/__tests__/sessions.test.ts`, a new fixture `lib/__tests__/agentScopeFixture.ts`

**Interfaces:**
- `sessions.ts`:
  - `export type ChatScope = { kind: 'document'; id: string; title: string } | { kind: 'agent'; id: string; name: string };`
  - `AgentOptions` gains `agent?: { id: string; name: string; revision: number; model: { id: string; name: string } | null; system_prompt: string; resources_fixed: boolean; subagents_allowed: boolean }`.
  - `getSessionOptions(scope: { documentId?: string; agentId?: string }, signal?)` → `/api/sessions/options?document_id=…` or `?agent_id=…` (replace the old `(documentId | null)` signature and update callers).
  - `getSessions({ agentId?, documentId?, purpose?, offset? }, signal?)` adds `agent_id`.
  - `reviewSession(id: string, comment: string, turn?: number)` → `POST /api/sessions/{id}/review` with `{comment, turn?}` returning `SessionReview` (`{ id, comment, suggestion, response_turn?, created_at, changes? }`).
  - `StartInput` gains `agent_id?: string`; `started_from`, `model_id`, `prompt_id`, `system_prompt` become optional.
- `agentSettings.ts`:
  - `useAgentSettings(scope: ChatScope | null)`: options by scope; storage key `ask-agent-settings:{id}` for a document (unchanged), `agent-settings:agent:{id}` for an agent; an agent's stored value carries `revision`, and `restoreSettings` returns the defaults when the stored revision differs from `options.agent.revision`.
  - `defaultSettings(options)` for an agent: `modelId` = the agent's model id, `promptKey` = `OWN`, `systemPrompt` = the agent's prompt, `chosen` = empty (a per-run agent starts with nothing chosen, as the Test tab did), `off` = [], `subagents` = `options.agent.subagents_allowed`, `experts` = every offered expert id.
  - `startInput(scope, options, settings, message)` (new signature; update Ask agent callers): a document → as today (`started_from`, `context: Document: {title} (id {id})`, `resources.documents`); an agent → `{ agent_id, message, context: '', tools: <shown tools not off>, expert_ids, subagents, resources: { ...settings.chosen, environment_ids: [], documents: [] } }` with no `model_id`, `prompt_id`, `system_prompt`, `started_from`.
  - `settingsSummary` unchanged.

- [ ] **Step 1: Write the failing tests**

Create `agentScopeFixture.ts` with an `AgentOptions` for a saved agent: `agent: { id: 'ops', name: 'Operations', revision: 3, model: { id: 'm1', name: 'GPT' }, system_prompt: 'You operate servers.', resources_fixed: false, subagents_allowed: true }`, `models: [{ id: 'm1', name: 'GPT' }]`, `default_model_id: 'm1'`, `prompts: []`, `default_prompt_id: null`, servers `[{ id: 's1', name: 'web-1', linked: false, usable: true, reason: null }]`, tools `ssh_run` (needs ssh) and `read_file` (needs files), one expert `e1`, other resource lists empty. Tests:

```ts
test('a saved agent starts with its own configuration and only narrows it', () => {
  const settings = { ...defaultSettings(agentOptions), chosen: { ...defaultSettings(agentOptions).chosen, server_ids: ['s1'] }, off: ['read_file'] };
  const body = startInput({ kind: 'agent', id: 'ops', name: 'Operations' }, agentOptions, settings, ' Check disk ');
  expect(body).toEqual({ agent_id: 'ops', message: 'Check disk', context: '', tools: ['ssh_run'], expert_ids: ['e1'], subagents: true,
    resources: { server_ids: ['s1'], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [], documents: [] } });
});

test('settings saved for an older revision of the agent are dropped', () => {
  const saved = { v: 2, revision: 2, linked: [], settings: { ...defaultSettings(agentOptions), off: ['ssh_run'] } };
  expect(restoreSettings(agentOptions, saved)).toEqual(defaultSettings(agentOptions));
  expect(restoreSettings(agentOptions, { ...saved, revision: 3 }).off).toEqual(['ssh_run']);
});
```

and in `sessions.test.ts`: `getSessionOptions({ agentId: 'ops' })` → `GET /api/sessions/options?agent_id=ops`; `getSessions({ agentId: 'ops' })` → `GET /api/sessions?agent_id=ops`; `reviewSession('s 1', 'Shorter', 2)` → `POST /api/sessions/s%201/review` with body `{ comment: 'Shorter', turn: 2 }`.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/lib/__tests__`
Expected: FAIL.

- [ ] **Step 3: Implement** as in **Interfaces**. The stored value becomes `{ v: 2, revision?: number, linked, settings }`. Update callers of the changed signatures (`AgentPane.tsx`, `AgentSettingsSheet.tsx`, `DocumentEditorView.tsx`, `SuggestionApply.tsx`) so `tsc` passes; behaviour for documents is unchanged.

- [ ] **Step 4: Run the checks**

Run: `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit`
Expected: PASS, except `components/__tests__/agentSessions.test.tsx` (the Test tab still calls the removed `testSessions` functions until Task 5) — if it fails only on those imports, note it; Task 5 rewrites it.

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-ui/app
git commit -m "feat(admin-ui): session options, list and review for saved agents; settings per chat scope"
```

---

### Task 4: The settings sheet for a saved agent

**Files:**
- Modify: `services/admin-ui/app/components/AgentSettingsSheet.tsx`
- Test: `services/admin-ui/app/components/__tests__/agentSettingsSheet.test.tsx`

**Interfaces:**
- Props: replace `documentId`/`documentTitle` with `scope: ChatScope` (Task 3); everything else unchanged.
- With `options.agent`: the description reads `What the agent may use in the next conversation.`; **Model** shows `options.agent.model?.name ?? 'No model'` as text and **Prompt** shows the agent's system prompt in a read-only `<pre>` under Advanced, each with `Change them in General.`; no prompt select, no system prompt textarea; **Allow subagents** only when `options.agent.subagents_allowed`; **Servers and resources**: when `options.agent.resources_fixed`, the line `This agent's resources are fixed.` instead of the lists; no Documents group. Tools, Experts, Preview, Done and the read-only open-run view work as for documents.

- [ ] **Step 1: Write the failing tests** (add to `agentSettingsSheet.test.tsx`, rendering the sheet with `agentOptions` from Task 3's fixture and `scope={{ kind: 'agent', id: 'ops', name: 'Operations' }}`):

```tsx
test('a saved agent shows its model and prompt read-only and offers only narrowing', async () => {
  renderSheet({ scope: agentScope, options: agentOptions, settings: defaultSettings(agentOptions) });
  expect(screen.getByText('GPT')).toBeInTheDocument();
  expect(screen.queryByRole('combobox', { name: 'Model' })).toBeNull();
  expect(screen.getAllByText('Change them in General.').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
  expect(screen.getByText('You operate servers.')).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: 'System prompt' })).toBeNull();
  expect(screen.getByRole('checkbox', { name: 'SSH' })).toBeChecked();
  expect(screen.getByRole('switch', { name: 'Allow subagents' })).toBeInTheDocument();
});

test("a fixed agent's resources are not offered", () => {
  const fixed = { ...agentOptions, agent: { ...agentOptions.agent!, resources_fixed: true, subagents_allowed: false } };
  renderSheet({ scope: agentScope, options: fixed, settings: defaultSettings(fixed) });
  expect(screen.getByText("This agent's resources are fixed.")).toBeInTheDocument();
  expect(screen.queryByRole('switch', { name: 'Allow subagents' })).toBeNull();
});
```

(`renderSheet` is the helper the file already uses, given the new `scope` prop; update existing calls to pass `scope={{ kind: 'document', id: 'doc-1', title: 'Runbook' }}`.)

- [ ] **Step 2: Run them to verify they fail**, then **Step 3: implement** as in **Interfaces**, **Step 4: run** `pnpm exec vitest run app/components/__tests__/agentSettingsSheet.test.tsx && pnpm exec tsc --noEmit` (PASS), **Step 5: commit** `feat(admin-ui): the settings sheet narrows a saved agent`.

---

### Task 5: `AgentChat` for every agent run; the Test tab uses it

**Files:**
- Rename: `services/admin-ui/app/components/AgentPane.tsx` → `AgentChat.tsx` (`git mv`; component `AgentChat`); update `DocumentEditorView.tsx` and the pane tests' imports/mocks (`agentPane.test.tsx`, `documentAgentPane.test.tsx`, `documentPageDrafts.test.tsx`)
- Create: `services/admin-ui/app/components/RunSuggestions.tsx` (the feedback form, saved suggestions and `SuggestionApply`, moved out of `AgentTestView.tsx`)
- Modify: `services/admin-ui/app/components/AgentTestView.tsx` (renders `AgentChat` with an agent scope)
- Test: rewrite `services/admin-ui/app/components/__tests__/agentSessions.test.tsx` for the new Test tab

**Interfaces:**
- `AgentChat` props: `{ scope: ChatScope; settings: ReturnType<typeof useAgentSettings>; initialRunId: string | null; confirmUnsaved?: (run: () => void) => void; onDraftChange?: (...) => void; onRunChange?: (runId: string | null) => void; blockedReason?: string | null; onAgentChanged?: () => void }`.
  - Region label: `Ask agent` for a document, `Test` for an agent. Empty text: today's for a document; `Send a message to test the agent.` for an agent.
  - Runs: `getSessions({ documentId })` or `getSessions({ agentId })`; a loaded run is foreign when (document) it is not `revision` for this document (today's rule) or (agent) its `agent_id` differs or its purpose is not `test`/`event`; foreign text for an agent: `This run belongs to another agent.`
  - Start: `startSession(startInput(scope, options, settings, text))`; `confirmUnsaved` wraps the first message only when given (documents).
  - `blockedReason` (agent: the Test tab's three reasons) blocks the composer when no run is open; it is shown above the message box, as today.
  - Document extras unchanged (`savedIn` note, draft callbacks). Agent extras: under each finished answer **Add feedback** (opens `RunSuggestions` on that turn); a header button **Suggestions (n)** (n = the run's reviews); `RunSuggestions` handles Suggest improvements (`reviewSession`), Copy suggestions, and Review and apply (`SuggestionApply`, `onApplied` as today: a clone navigates to `/agents/{id}?view=test`, applying to the agent shows `Applied to this agent. New conversations use it.` and calls `onAgentChanged`).
- `RunSuggestions` props: `{ agentId: string; session: SessionDetail; turn: number | null; open: boolean; onOpenChange: (open: boolean) => void; onReview: (review: SessionReview) => void; onApplied: (target: 'agent' | 'clone', id: string) => void }` — the sheet content from today's `feedback` panel, verbatim copy.
- `AgentTestView` keeps its props; it renders `<AgentChat scope={{ kind: 'agent', id: agentId, name: agentName }} settings={useAgentSettings(active ? scope : null)} initialRunId={params.get('session_id')} onRunChange={…sets ?view=test&session_id=…} blockedReason={…today's three reasons…} onAgentChanged={onAgentChanged} />` full width (no viewport pane), and nothing else: History, Show config and the resource picker are gone (Earlier runs, Preview and the sheet replace them).

- [ ] **Step 1: Rename and keep the pane green**

`git mv` the component and its usage; run `pnpm exec vitest run app/components/__tests__/agentPane.test.tsx app/components/__tests__/documentAgentPane.test.tsx app/components/__tests__/documentPageDrafts.test.tsx` — PASS with only import/mock renames.

- [ ] **Step 2: Write the failing Test-tab tests** (rewrite `agentSessions.test.tsx`; keep its fetch-stub helpers and router setup):

```tsx
test('the first message starts a run of the saved agent with the narrowed settings', async () => {
  const calls = serve(); // options for agent 'ops' (Task 3 fixture shape), empty list, then the started session
  renderTestTab();
  fireEvent.change(await screen.findByRole('textbox', { name: 'Message' }), { target: { value: 'Check disk' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await waitFor(() => expect(calls.find(c => c.key === 'POST /api/sessions')?.body).toMatchObject({ agent_id: 'ops', message: 'Check disk' }));
  expect(calls.find(c => c.key === 'POST /api/sessions')?.body).not.toHaveProperty('system_prompt');
});

test('the test tab cannot start while the configuration tabs have unsaved changes', async () => {
  serve(); renderTestTab({ dirty: true });
  expect(await screen.findByText('Save or discard changes in the configuration tabs before starting a task.')).toBeInTheDocument();
});

test('feedback on an answer asks for suggestions and they can be reviewed and applied', async () => {
  const calls = serve({ run: finishedRun });  // an open test run with one finished turn
  renderTestTab({ sessionId: 'r1' });
  fireEvent.click(await screen.findByRole('button', { name: 'Add feedback' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Your feedback' }), { target: { value: 'Be shorter' } });
  fireEvent.click(screen.getByRole('button', { name: 'Suggest improvements' }));
  await waitFor(() => expect(calls.find(c => c.key === 'POST /api/sessions/r1/review')?.body).toEqual({ comment: 'Be shorter', turn: 1 }));
  expect(await screen.findByRole('button', { name: 'Review and apply' })).toBeInTheDocument();
});

test('earlier runs replace History', async () => {
  serve({ runs: [closedRun] }); renderTestTab();
  expect(await screen.findByRole('button', { name: 'Earlier runs' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: 'History' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Show config' })).toBeNull();
});
```

Keep the existing tests that still apply (blocked reasons, close, foreign runs), adapted to the shared chat.

- [ ] **Step 3: Run them to verify they fail**, **Step 4: implement** as in **Interfaces**, **Step 5: run** the full UI suite, `tsc` and `react-router routes` (PASS), and `grep -rn "testSessions\|AgentPane\b\|createTestSession\|getTestSessions\|reviewTestSession\|getRunResourceOptions" services/admin-ui/app` (only `getRunResourceOptions` may remain if Generate documents still uses it), **Step 6: commit** `feat(admin-ui): one chat for every agent run; the Test tab uses it`.

---

### Task 6: Docs and the final check

**Files:**
- Modify: `docs/user-guide.md` (the agent Test tab section: the shared chat, Settings narrowing, Earlier runs, Add feedback/Suggestions), `docs/agents.md` (the Test session API → `/api/sessions` with `agent_id`, the narrowing rule, the review route), `deploy/README.md` (the Test routes line), `docs/documentation.md` if it names `/api/agents/{id}/sessions`.

- [ ] **Step 1: Update the docs** — every `/api/agents/{id}/sessions…` becomes the `/api/sessions` equivalent; describe the narrowing rule in plain words (a Test run can switch the agent's tools off, choose some of its experts, turn subagents off and, for a per-run agent, choose from its resources; nothing can be added; the model and prompt are the agent's own — change them in General, or clone the agent to try another prompt).
- [ ] **Step 2: Final search**

```bash
git grep -n "api/agents/[^ ]*/sessions\|createTestSession\|getTestSessions\|reviewTestSession\|testSessions\|NO_SAVED_AGENTS\|Saved agents start from their Test tab\|AgentPane\b" -- . ':!docs/superpowers'
```

Expected: no output (old-route 404 assertions in tests may mention the path; list them in the report).

- [ ] **Step 3: Run everything** — backend full suite and the UI checks; PASS apart from the known failures.
- [ ] **Step 4: Commit** `docs: saved agents start through /api/sessions; the Test tab uses the shared chat`.
