# Code project documentation into named folders — design

Date: 2026-10-07. Status: draft for review.

## Goal

Generate a document for one code project with a chosen prompt and comments, and save it
into a named documentation folder. A documentation folder is made searchable in
**RAG → Collections**: a new collection can be created with that folder already selected,
and it is synced right away.

## Flow

1. **Docs → Generate documents → New → Code project**, a third choice next to
   **Service** and **Whole environment**.
2. **The form:**

   | Field | Notes |
   | --- | --- |
   | Code project | One project. If it has no code graph, or the code changed since the graph was built, the form shows **Generate graph** first, the same Phase 1 used today |
   | Prompt | Any active documentation prompt whose input is code or mixed. Server-only prompts are hidden: the run has no server access |
   | Comments | Optional extra instructions for this run (up to 20,000 characters) |
   | Document name | The document title, for example "Payments – API reference" |
   | Documentation folder | A folder under `GENERATED_DOCS_FOLDER`. Pick an existing one or type a new name, for example `payments` or `team/payments`; the field is always editable. The default is the project's name as a slug plus `-docs` (`Payments API` → `payments-api-docs`). Same segment rules as RAG folders: visible names of letters, digits, `.`, `_` and `-`; no `node_modules` segment and no first segment `services` (where the service phase exports live) |

3. **Generate** queues a documentation job. The agent:
   - reads that project's code and graph;
   - follows the chosen prompt, with the comments appended;
   - has no server access, no RAG and no MCP tools.
4. **The result:**
   - The document is saved in **Docs → Documents** (PostgreSQL stays the source of
     truth, with versions), linked to the code project, with its prompt, model and
     source commits recorded.
   - It is exported to `GENERATED_DOCS_FOLDER/<folder>/<document-slug>/README.md`, with
     diagrams and metadata in the same folder as today's exports. The slug comes from the
     document name.
   - The run appears in the generation history.
5. **Same name in the same folder:** you are asked to confirm. Confirming saves a new
   version of that document. Otherwise the run is refused with 409.

## Documentation folders

- **Docs → Folders**, a new browse page, lists the folders under `GENERATED_DOCS_FOLDER`:
  - each folder's document count;
  - read-only, the RAG collections that include it: collections whose folders (from
    `GET /api/rag/collections?status=false`) are `generated-documents/<folder>` itself or a
    parent of it (for example all of `generated-documents`), each linking to its collection
    page; **Not indexed** when there are none;
  - a link **Index in RAG → Collections** to
    `/rag/collections?folder=generated-documents/<folder>`, which opens **New collection**
    with that folder selected.
- **Opening a folder** shows its documents (title, code project, last generated). Each
  one opens in Docs → Documents.
- **Indexing lives in RAG.** **RAG → Collections → New collection** gets an optional
  **Folders** selection: the existing folder picker over `DATA_ROOT`, one or more folders.
  **Create collection** creates the collection, connects each selected folder and queues
  a sync at once, so the data is synced and processed right away. It chains RAG's existing
  API from the admin UI: `POST collections`, then `POST collections/{id}/paths` per
  folder, then `POST collections/{id}/sync`. With no folders selected, creating works as
  today (no sync).
- **A folder that cannot be added** (missing, skipped, overlapping another selected
  folder): the error is shown and the created collection is kept; the page opens that
  collection so the admin can fix its folders. No sync is queued then.
- Connecting, disconnecting and syncing existing collections stay on the collection page
  as today. Disconnecting a folder keeps its files.
- **No copying.** RAG already sees all of `DATA_ROOT`, so connecting a folder indexes the
  documents where they are. A copy into another input folder would duplicate files and
  go stale whenever a document is regenerated. After a new version is exported, the
  collection shows **Needs refresh**, and auto-sync picks it up when it is on.

## Model and API

- **Generation input:** gains `scope: 'code_project'` with `code_project_id`,
  `prompt_id`, `prompt_version_id`, `comments`, `document_name`, `folder` and
  `update_existing`. Existing service and environment generation are unchanged.
- **Documents:**
  - **Folder:** a document records its documentation folder (new nullable column
    `ka.documents.folder`). Documents without a folder export as today.
  - **Slugs:** unique per folder. Existing slugs stay unique because they have no
    folder.
- **New admin-api routes** (read-only; they never call RAG):

  | Route | Purpose |
  | --- | --- |
  | `GET /api/documentation/folders` | List folders with document counts and their RAG folder path (`generated-documents/<folder>`) |
  | `GET /api/documentation/folders/{folder}` | List one folder's documents |

  Folder names are validated with the same rules as above (no `..`, hidden names or
  links). The RAG service is unchanged: New collection uses its existing API
  (`POST collections`, `POST collections/{id}/paths`, `POST collections/{id}/sync`) through
  the admin RAG proxy.

## Errors

| Problem | Behaviour |
| --- | --- |
| No code graph, or a stale one | Generate is disabled until Phase 1 finishes |
| Prompt changed since the form loaded | 409 "Reload the prompt" |
| Same document name in the folder without confirming | 409, with the option to save a new version |
| Bad folder name | 400 naming the rule |
| RAG unavailable when creating a collection | The error is shown, nothing is created |
| A selected folder cannot be added to the new collection (missing, skipped, overlapping another selected folder) | RAG's error is shown; the created collection is kept and opened, with no sync queued |

## Testing

- **admin-api:**
  - input validation;
  - generation queued with the right snapshot: code tools on, no SSH, the prompt and
    the comments;
  - the export path `<folder>/<slug>/README.md`;
  - same name in the same folder: refused, or a new version when confirmed;
  - the folder list and folder documents;
  - folder name validation.
- **Migration:** `documents.folder` up and down, and per-folder slug uniqueness.
- **admin-ui:**
  - the Code project choice;
  - the prompt filter;
  - graph-first gating;
  - same-name confirmation;
  - the `<project>-docs` default folder;
  - the Folders page with the read-only collections and the **Index in RAG →
    Collections** link;
  - RAG **New collection** with folders (connect and sync), a folder that fails (collection
    kept and opened with the error) and the `?folder=` prefill.
- **Docs:** the user guide's documentation and RAG sections.

## Out of scope

- Several projects in one document.
- Editing the prompt text per run (comments cover per-run guidance).
- Automatic RAG sync after generation, beyond the collection's own auto-sync setting.
