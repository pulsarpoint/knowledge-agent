# Skill library

Administration → **Skills** (`/skills`) stores reusable agent instructions in PostgreSQL. Administrators can add skills, open and edit them, preview their Markdown, filter by name/description/status, and enable or disable them from the list.

Each skill has a stable UUID, a unique lowercase name (letters, digits and single hyphens, up to 64 characters), a required description explaining when to use it (up to 1,024 characters), Markdown instructions (up to 100,000 characters), an enabled flag, and a revision number. Markdown whitespace is preserved. Created/updated timestamps and administrator identity are recorded.

Disabling retains the content and the skill remains editable. Text edits preserve the enabled state when the API caller omits it. Enabling, disabling, and editing require the current revision; conflicting changes return HTTP 409 without replacing newer content. The status switch writes only the flag, not the instruction body. There is no delete operation.

## API

All routes require the admin gateway and the exact administrator group. Mutations also require the authenticated user identity.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/skills` | Summary list, excluding instruction bodies |
| GET | `/api/skills?enabled=true` | Enabled skills available for selection |
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

An agent uses a skill when its access object (`ka.agents.access`) references it (`{"type": "skill", "id": ...}`). Saving a skill in the library does not automatically assign it; one agent may reference many skills and one skill may be referenced by many agents. Agent instructions are in General.
