# Clone agents instead of versions — design

Date: 2026-10-08. Status: approved in conversation (user decisions below).

## Goal

An agent has **one configuration**: what its tabs show is what runs. Agent configuration versions and agent
prompt-version history are removed. Experiments are done on a **clone**: clone an agent, change and test the clone, then
**Apply to original** (or keep the clone as an alternative, or delete it).

User decisions:
- Remove agent configuration versions (**Versions** tab, **Test vN**, "Choose version", drafts, activation).
- Remove the agent **Prompts** tab's draft/published prompt history; the agent's prompt becomes one text field.
- **Apply to original** copies everything except name and description.

Out of scope: **Docs → Prompts** (the documentation prompt library) stays as it is; it is not an agent's prompt.

## Agent configuration (one source)

- Everything that runs comes from the agent itself: Agent type, Resources mode, model, prompt, additional
  instructions, skills, tools (with usage text), code projects, documents, SSH servers (per-server instructions and
  connection overrides), MCP servers, services. No "active version" rule anywhere.
- **Prompt:** one text field on the agent (`ka.agents.system_prompt text NOT NULL`), edited on **General** (or a
  **Prompt** card there). The Prompts tab, `ka.agent_prompts` and `ka.agent_prompt_versions` go away for agents.
- **Runs and conversations keep their exact configuration:** sessions (Test chats and documentation sessions) and
  runs already store the configuration snapshot they ran with; that stays, so history remains accurate. Version
  references (`agent_version_id`, `version_id`) are dropped from them.

## Clone

- **Clone agent** on the agent page (header menu) and in the All agents row menu.
- Creates a new agent named "<name> (copy)" ("(copy 2)" … if taken) with an identical configuration: all fields above,
  tool rows, skills, collections, code projects, services, SSH server selections with instructions and connection
  overrides, MCP selections. Saved SSH credentials are **referenced**, not duplicated. The clone keeps the
  original's enabled state.
- The clone records `ka.agents.cloned_from` (nullable FK to the original, `ON DELETE SET NULL`), shown on its General
  tab as "Clone of <original>".
- Not cloned: sessions, runs, history, audit.

## Apply to original

- Shown on a clone whose original still exists: **Apply to <original>**.
- A confirmation dialog lists what will change (sections that differ: prompt, instructions, model, type, resources,
  tools, skills, documents, code projects, SSH, MCP, services).
- Copies the clone's configuration onto the original in one transaction, except name, description and
  `cloned_from`; bumps the original's revision; audit `agent.config.applied` with both ids.
- Refused while the original is working (same check as delete), and with 409 if the original changed since the dialog
  opened (revision check).
- After applying, the clone offers **Delete this clone**.

## Feedback suggestions

- "Add feedback → Suggest improvements" in a Test chat stays. Its proposals now offer **Apply to this agent** (after
  review, writes the proposed instruction/prompt changes into the agent) or **Apply to a new clone** (clone first,
  then write them there).

## Migration

- For every agent with an active configuration version that differs from its working copy, the active version's
  configuration becomes the agent's configuration (it is what runs today) — including its tools, resources mode,
  runtime-related fields and selections. Then the active prompt version's text becomes `ka.agents.system_prompt`.
- Drop `ka.agent_versions`, `ka.agent_prompts`, `ka.agent_prompt_versions`, `active_config_version_id`, version
  references on sessions/runs/reviews, and their triggers/constraints/functions (including seed functions that
  create prompt versions for new agents — new agents get their prompt directly).
- Down restores the empty schema only (history not restored); documented in deploy/README.

## Screens

- Agent page tabs: General (with the prompt), Tools, Skills, Documents, Code projects, SSH, MCP, Services, Test.
  No Prompts, no Versions.
- Test tab: no "Choose version"; a conversation pins the agent's configuration when it starts (as today).
- All agents list: row menu gains **Clone**; a clone shows "Clone of <original>" under its name.
- Wizard: the system prompt it generates is saved as `system_prompt`.

## Errors

| Problem | Behaviour |
| --- | --- |
| Apply when the original was deleted | Button hidden; API 404 |
| Apply while the original is working | 409 with the "agent is working" message |
| Original changed since the dialog opened | 409 "The original changed; review again" |
| Clone name collision | "(copy N)" suffix |

## Testing

- Migration: active version materialised into the agent (incl. tools and selections), prompt text moved, tables and
  columns dropped, existing sessions still open with their pinned config; new agents/projects still creatable.
- Clone: identical effective configuration (compare `get_agent_config` of both, minus ids/names), credentials
  referenced not copied, history not copied.
- Apply: copies all sections except name/description, revision bump, audit, 409 cases, refused while working.
- Feedback: apply to agent / apply to new clone.
- UI: tabs without Prompts/Versions; prompt editing on General; Clone in both menus; Apply dialog with the change
  list; no version picker in Test.
- Every place that read versions or prompt versions (sessions, wizard, list, profiles, documents/prompts APIs) works
  from the agent.
