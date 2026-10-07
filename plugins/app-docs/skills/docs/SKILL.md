---
name: docs
description: Build or update application documentation in Diátaxis format (tutorials, how-to guides, reference, explanation) by analyzing the application's code and asking clarifying questions only where code cannot answer.
argument-hint: "[build|update] <application-root-path> <docs-folder-path>"
disable-model-invocation: true
---

# Application documentation (Diátaxis)

Before acting, read completely:

- `../../references/interview-workflow.md`
- `references/guide.md`
- `../../references/chunk-contract.md`

All three paths are required. `build` performs a full analysis and creates the
documentation set; `update` refreshes only documents affected by source
changes since each document's recorded `analyzed_commits`, following the
workflow's update mode. Reader gaps are no longer collected (the Reader
application was removed), so there is no gaps file to read. When the mode is
omitted, use `update` if
`<docs-folder>/index.md` exists and `build` otherwise.

Follow the interview workflow exactly. Use the guide for the output
layout, per-quadrant checklists, and writing rules. A single run analyzes
the application once and maintains all four quadrants together.

## Chunk-contract feedback loop

After writing or updating all documents, run the `rag index` command on the docs
folder to check for chunk-contract violations:

```text
cd services/rag && uv run rag index <docs-folder>
```

or from the repository root, use the convenience target:

```text
make index TARGET=<docs-folder>
```

Alternatively, ask the operator to run either command when `rag` is available.
Treat `rag index: warning:` lines as review findings — feed each back into a
revision pass until the report is clean. For each warning, revise the cited section
according to the guidance in `../../references/chunk-contract.md`:

- **Section split warning:** Split the section into smaller H3 subsections, each
  under 1800 characters.
- **No-headings warning:** Add an H1 title and/or H2/H3 section headings as needed.
- **Duplicate heading warning:** Rename duplicate headings to be distinct and specific.

If a warning describes a violation you intentionally accept (e.g., a large reference
table), record that choice in the document's YAML front matter using the
`chunk_contract_accepted` key:

```yaml
---
analyzed_commits: ...
updated: ...
chunk_contract_accepted:
  - "API response reference"  # large table, split unavoidable
---
```

Repeat `rag index` until the report is clean, or every remaining warning is
recorded as an accepted exception in `chunk_contract_accepted`, then proceed
with your normal staging, commit, and merge workflow.
