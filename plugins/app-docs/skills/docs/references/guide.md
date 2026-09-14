# Diátaxis authoring guide

## Output layout

```text
<docs-folder>/
  index.md                documentation map + application overview
  tutorials/              optional; may be deliberately absent
  how-to/<task>.md        task-oriented guides
  reference/<area>.md     services, APIs, configuration, data
  explanation/<topic>.md  architecture, domain, decisions
```

`index.md` lists every document with a one-line description, states which
quadrants are deliberately thin or absent and why, and records open
questions. Tutorials are expected to be thin or absent for internal
applications — record that as a deliberate omission, never leave it
implicit.

## Quadrant checklists

**Reference** (usually the largest quadrant — facts, stated plainly):

- one document per service: purpose, key packages, entry points;
- API surface: HTTP/gRPC endpoints, message topics consumed and
  produced, scheduled jobs — method, path/topic, handler location;
- configuration: keys, where each is read, defaults, required vs
  optional — never secret values;
- data: stores, key entities/tables, ownership per service;
- external dependencies: third-party services and what they are used
  for.

**Explanation** (reasons and background):

- system architecture: services, responsibilities, how they communicate;
- domain concepts a new engineer must know;
- significant decisions and their reasons — human input usually
  required; draft what code implies, ask for the why;
- cross-service flows described in prose.

**How-to** (steps toward a goal, one task per document):

- operational tasks evident from code and configuration: run locally,
  deploy, migrate the database, add a new service, rotate credentials
  (conceptually — mechanisms, never values);
- tasks the developer names during the interview;
- each guide opens with its goal and preconditions, then numbered steps.

**Tutorials** (learning paths):

- only when a genuine learning path exists (for example local
  development onboarding end to end);
- otherwise record the deliberate omission in `index.md`.

## Writing rules

- One document per task, area, or topic; split rather than mix.
- Quadrant purity: reference states facts, how-to gives steps,
  explanation gives reasons. Link across quadrants instead of blending
  them in one document.
- Plain Markdown, no HTML. Headings start at `#` per document.
- Citations sit adjacent to the claims they support, in the workflow's
  format. Human-confirmed facts are attributed inline.
- Every document begins with the front matter defined by the workflow
  (`analyzed_commits`, `updated`).
- Write for a support engineer meeting the application for the first
  time: define terms on first use, prefer concrete names from the code
  over abstractions.
