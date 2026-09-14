# Application Docs (Diátaxis)

One skill that produces application documentation in
[Diátaxis](https://diataxis.fr/) format — tutorials, how-to guides,
reference, explanation — by analyzing the application's code and asking
the developer only what code cannot answer.

## Installation (existing Claude Code)

From any Claude Code session, register this repository as a plugin
marketplace and install the plugin (use the path where you cloned the
repository; a git remote URL works the same once one exists):

```text
/plugin marketplace add /Users/graovic/fincore/knowledge-agent-v2
/plugin install app-docs@knowledge-agent-v2
```

The skill is available immediately as `/app-docs:docs` — check with
`/plugin` → Manage plugins, or start typing `/app-docs` in the prompt.
To update after pulling changes, reinstall with the same command; to
remove: `/plugin uninstall app-docs@knowledge-agent-v2`.

## Quick start

With local clones of the application (one repository, or a folder of
service repositories) and a docs repository:

```text
/app-docs:docs build /work/my-app /work/my-app-docs
```

After application code changes:

```text
/app-docs:docs update /work/my-app /work/my-app-docs
```

`update` refreshes only the documents affected by source changes since
each document's recorded `analyzed_commits` (kept in the document's front
matter — there is no other state). If the docs folder is served by
docs-wiki, `update` also picks up the gaps file — the questions readers
asked that the documentation could not answer, each one already carrying
a code-derived draft answer and citations when the reader escalated it
through docs-wiki's optional "Ask the code" — from the path the
developer provides (documented default
`~/.local/state/docs-wiki/<app>/gaps.jsonl`, docs-wiki's central state
directory; falls back to the legacy `<docs-folder>/.docs-wiki/gaps.jsonl`
when present), verifies and lands each draft (or answers from code fresh
where there isn't one) where possible, and brings the rest into the
interview; the run's summary lists which gaps were addressed.

## What it produces

```text
my-app-docs/
  index.md                documentation map + overview + open questions
  tutorials/              optional
  how-to/<task>.md
  reference/<area>.md
  explanation/<topic>.md
```

Every factual claim carries an inline citation
`(<service>@<commit>:<path>:<line>)`; facts confirmed by the developer
are attributed inline. Documentation is written on a local branch
(`docs/update-<YYYYMMDD>`) and the skill stops before staging — commit,
push, and merge request follow your normal review process.

## How it works

The skill follows [references/interview-workflow.md](references/interview-workflow.md):
read existing docs, analyze code first, draft complete documents, present coverage and
the draft, ask at most three focused questions per turn (each with a
code-suggested answer), then propose the full change set and write only
after confirmation. The Diátaxis quadrant checklists and writing rules
live in [skills/docs/references/guide.md](skills/docs/references/guide.md).

## Chunk contract and indexing feedback loop

Documentation is indexed by the `rag` retrieval service, which chunks sections (H2/H3 headings) into units for deep-linking and hybrid search. Every section must be self-contained (no back-references), phrased as a question or intent, and kept under ~1800 characters to avoid splitting during indexing. After writing or updating documents, run `rag index <docs-folder>` to check for violations: section splits, missing headings, or duplicate heading text. Each warning is a revision signal — see [references/chunk-contract.md](references/chunk-contract.md) for the authoring rules and feedback loop details, including how to record intentional exceptions.

## Source analysis

The v3 agent reads source repositories directly for deeper code analysis.
