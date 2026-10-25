# Skill library

Administration → **Skills** (`/skills`) stores reusable agent instructions in PostgreSQL. Administrators can add skills, open and edit them, preview their Markdown, filter by name/description/status, and enable or disable them from the list.

Each skill has a stable UUID, a unique lowercase name (letters, digits and single hyphens, up to 64 characters), a required description explaining when to use it (up to 1,024 characters), Markdown instructions (up to 100,000 characters), an enabled flag, and a revision number. Markdown whitespace is preserved. Created/updated timestamps and administrator identity are recorded.

Disabling retains the content and the skill remains editable. Text edits preserve the enabled state when the API caller omits it. Enabling, disabling, and editing require the current revision; conflicting changes return HTTP 409 without replacing newer content. The status switch writes only the flag, not the instruction body. There is no delete operation.

## API

All routes require the admin gateway and the exact administrator group. Mutations also require the authenticated user identity.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/skills` | Summary list, excluding instruction bodies |
| GET | `/api/skills?enabled=true` | Enabled skills (the ones every run gets) |
| GET | `/api/skills?enabled=false` | Disabled skills |
| GET | `/api/skills/{id}` | Complete skill with Markdown instructions |
| POST | `/api/skills` | Create a skill; returns HTTP 201 |
| PUT | `/api/skills/{id}` | Update a skill using its current revision |
| PATCH | `/api/skills/{id}/status` | Enable/disable using `enabled` and `revision` |

Create example:

```json
{
  "name": "inspect-service-logs",
  "description": "Use when investigating errors in a running service.",
  "instructions": "# Inspect logs\n\n1. Identify the service and time window.\n2. Review relevant logs.\n3. Summarize findings with evidence.\n",
  "enabled": true
}
```

Updates use the same fields plus `revision`. Status changes use `{"enabled": false, "revision": 1}`. Successful changes return the updated record and revision. Metadata changes are recorded in `ka.audit_events`; full instructions are not copied into audit messages. Migration `0011_agent_skills.up.sql` creates `ka.agent_skills` with uniqueness and content constraints.

**Every agent sees every enabled skill.** Skills are not chosen per agent and are not part of the access model: every
run (saved agents, ad-hoc chats, event and schedule runs, Test, Run agent, documents, reviews) gets all enabled skills
of the project, read once at run start and pinned in its configuration as `skills` (`[{id, name, description,
folder}]`, in name order; a skill whose folder is missing on disk is left out). To hide a skill from agents, disable
it; the Skills page says "Every agent sees every enabled skill; disable a skill to hide it." A skill enabled, disabled
or updated after a run started does not change that run. Subagents and experts get the skills of the run that calls
them.

**Listed at creation, read on use.** A run's instructions list its skills, each by name, description and the
absolute path of its `SKILL.md`, in a **Skills** section (a run without skills, or with `read_file` switched off, has
none); the instruction text never goes into the prompt. When the agent uses a skill (the person names it, or the task
matches its description) it reads that `SKILL.md` with `read_file`, then follows it; the skill's other files are in the
same folder (`list_files`, `read_file`) and its scripts run with `shell_run`. Debug and the session detail show the
skills used: those whose `SKILL.md` the run read.
