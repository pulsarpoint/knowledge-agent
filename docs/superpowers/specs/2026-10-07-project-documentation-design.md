# Code project documentation into named folders — design

Date: 2026-10-07. Status: draft for review.

## Goal

Generate a document for one code project with a chosen prompt and comments, and save it
into a named documentation folder. A documentation folder can then be made searchable
by connecting it to a RAG collection.

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
   | Documentation folder | A folder under `GENERATED_DOCS_FOLDER`. Pick an existing one or type a new name, for example `payments` or `team/payments`. The default is the project's name. Same segment rules as RAG folders: visible names of letters, digits, `.`, `_` and `-` |

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

- **Docs → Folders**, a new page, lists the folders under `GENERATED_DOCS_FOLDER`:
  - each folder's documents (title, project, last generated) and document count;
  - its RAG state: **Not indexed**, or the collections it is connected to, with each
    collection's status (Up to date, Needs refresh and so on).
- **Opening a folder** shows its documents. Each one opens in Docs → Documents.
- **Index in RAG** connects the folder to a collection:
  - pick an existing collection, or create a new one with a name (default: the folder
    name) and default settings;
  - RAG adds `generated-documents/<folder>` (the path relative to `DATA_ROOT`) as a
    collection folder and queues a sync;
  - **Disconnect** removes the folder from that collection; the files stay.
- **No copying.** RAG already sees all of `DATA_ROOT`, so connecting a folder indexes the
  documents where they are. A copy into another input folder would duplicate files and
  go stale whenever a document is regenerated. After a new version is exported, the
  collection shows **Needs refresh**, and auto-sync picks it up when it is on.
- **Overlap:** a folder whose parent or child is already connected to the same collection
  is refused by RAG's existing overlap rule (409, shown on the page).

## Model and API

- **Generation input:** gains `scope: 'code_project'` with `code_project_id`,
  `prompt_id`, `prompt_version_id`, `comments`, `document_name`, `folder` and
  `update_existing`. Existing service and environment generation are unchanged.
- **Documents:**
  - **Folder:** a document records its documentation folder (new nullable column
    `ka.documents.folder`). Documents without a folder export as today.
  - **Slugs:** unique per folder. Existing slugs stay unique because they have no
    folder.
- **New admin-api routes:**

  | Route | Purpose |
  | --- | --- |
  | `GET /api/documentation/folders` | List folders with document counts and the collections connected to each |
  | `GET /api/documentation/folders/{folder}` | List one folder's documents |
  | `POST /api/documentation/folders/{folder}/index` with `{collection_id}` or `{new_collection: {name}}` | Connect the folder in RAG and queue a sync |
  | `DELETE /api/documentation/folders/{folder}/index/{collection_id}` | Disconnect the folder |

  Folder names are validated with the same rules as above (no `..`, hidden names or
  links). These routes use RAG's existing API (`POST collections`,
  `POST collections/{id}/paths`, `DELETE collections/{id}/paths/{path_id}`,
  `POST collections/{id}/sync`, `GET collections?status=false`) through the backoffice
  client.

## Errors

| Problem | Behaviour |
| --- | --- |
| No code graph, or a stale one | Generate is disabled until Phase 1 finishes |
| Prompt changed since the form loaded | 409 "Reload the prompt" |
| Same document name in the folder without confirming | 409, with the option to save a new version |
| Bad folder name | 400 naming the rule |
| RAG unavailable when indexing | 502, and nothing changes |
| Folder overlaps one already connected to that collection | 409 from RAG, shown |

## Testing

- **admin-api:**
  - input validation;
  - generation queued with the right snapshot: code tools on, no SSH, the prompt and
    the comments;
  - the export path `<folder>/<slug>/README.md`;
  - same name in the same folder: refused, or a new version when confirmed;
  - the folder list and folder documents;
  - index and disconnect against a fake RAG (existing or new collection, overlap 409,
    RAG down 502);
  - folder name validation.
- **Migration:** `documents.folder` up and down, and per-folder slug uniqueness.
- **admin-ui:**
  - the Code project choice;
  - the prompt filter;
  - graph-first gating;
  - same-name confirmation;
  - the Folders page with **Index in RAG** (existing or new collection) and
    **Disconnect**.
- **Docs:** the user guide's documentation and RAG sections.

## Out of scope

- Several projects in one document.
- Editing the prompt text per run (comments cover per-run guidance).
- Automatic RAG sync after generation, beyond the collection's own auto-sync setting.
