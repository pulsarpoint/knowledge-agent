# Documentation updates from commits

`knowledge_admin.documentation_updates.propose_service_documentation_update` is a
standalone asynchronous function. It uses the existing Agents SDK runner and a
model definition from LLM Settings, including Bedrock. It does not query the
database, fetch Git history, or publish documents.

```python
from pathlib import Path
from knowledge_admin.documentation_updates import propose_service_documentation_update

result = await propose_service_documentation_update(
    service={"id": service_id, "name": "Responsible Gaming"},
    repository_path=Path("/work/code/responsible-gaming"),
    base_commit=documented_commit,
    target_commit="HEAD",
    documents={
        "overview": current_overview_markdown,
        "api": current_api_markdown,
    },
    document_commits={"overview": documented_commit, "api": documented_commit},
    model=model_from_llm_settings,
    credentials=resolved_model_credentials,
    instructions="Update affected API contracts and state transitions.",
)
```

The caller supplies the saved model dictionary and credentials keyed by its
`api_key_ref.id`, as with the existing agent runner. Keys are backend inputs and
are not placed in the prompt. Document keys can be existing database document IDs
or caller-defined names; returned updates use those same keys.

Each input document must record the exact full base commit hash in
`document_commits`. A missing or mismatched baseline is rejected before calling
the model. Do not infer the revision of an old report from today's HEAD: verify
its source first. `document_commits` in the result records the target revision.

Document creation/update accepts `source_commits`, a code-project UUID to full
Git hash mapping. It is stored as `git_commit` on each code project in the
immutable version's existing `source_snapshot` JSON; no new table is needed.
Omitting this parameter during an ordinary edit preserves the previous hashes;
an explicit empty mapping clears them. Historical versions retain their hashes.
Generation captures hashes automatically for clean Git-backed mapped folders,
accepts explicit expected `source_commits`, and checks them before and after
analysis. Dirty or unversioned folders remain unpinned rather than being
misrepresented as a committed tree.

The function resolves both revisions to immutable commit IDs and compares the
base tree directly with the target tree (not a merge base). `HEAD` is the latest
**local** commit. Both commits and their blobs must exist locally. The current
sync worker uses shallow fetches: a missing base requires fetching that history
through the existing authenticated Git setup before calling this function.
For a service backed by multiple repositories, call it separately for each
repository and supply the relevant documents.

The agent receives paths to the patch, changed-file manifest, both committed
source snapshots and the existing Markdown. It can use the existing file,
code-search, ast-grep and isolated shell tools to inspect focused source ranges.
Uncommitted changes and Git credentials/configuration are not copied. Git hooks,
checkout filters and external diff programs are not executed. Symlinks and
submodules are reported as unavailable rather than followed. Binary changes are
listed; they may require manual interpretation. The graph for the working copy
is not reused for historical revisions.

Temporary evidence is created under `DOCUMENT_OUTPUT_ROOT`, which is already
shared read-only with the shell worker in Compose. Local calls without a shell
worker can use the system temporary directory. Evidence is removed after the
call, including failures and cancellation. No checkout or saved document is
changed. With identical source trees, no model call is made.

The returned object contains:

- `service`, `base_commit`, `target_commit`, and `changed_files` for provenance.
- `summary` and `updates`: each update has `document_key`, complete replacement
  `markdown`, exact `edits`, `reason`, and commit-relative `evidence` citations.
- `unresolved_questions` and `skipped_source_entries` for missing evidence.
- `token_usage` from the SDK, or `null` when no model call was needed.
- `change_report_markdown`, a separate document explaining the commit comparison,
  affected source files, documentation changes and supporting evidence.

Updates are proposals: inspect them before saving a new document version through
the existing document API. The function rejects unknown document keys and
nonunique/missing edit anchors. Multiple updates for one document are combined
in order, retaining every edit and its evidence. The agent proposes exact text replacements;
the function assembles the full Markdown, preserving all untouched text. Missing
or ambiguous anchors get one correction using the completed conversation, with at most
five further model turns and the same overall timeout. If the correction is still
invalid, nothing is changed. Token usage includes both calls. Source
citations and factual accuracy still require review. A zero-update
result can mean identical trees or changes that do not affect the documentation;
the summary distinguishes these cases. `on_activity` and `on_usage` callbacks are
available for a future job/UI integration.
