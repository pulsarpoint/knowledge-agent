# UX Consistency — Audit and Standards (spec)

Approved 2026-10-27. This audit is the spec for `docs/superpowers/plans/2026-10-27-ux-consistency.md`.

**User rulings (override the audit where they differ):**
- A draft goes live with **Publish** for documents (Promote → Publish) and **Confirm** for inventory. The audit's §10 "Confirm for every draft, documents included" is overruled.
- **Chat** is the conversation with an agent; **run** is one execution (Agent runs records).
- Phases: 1 = §1, §3, §7, §9 (toasts) and §10 terminology; 2 = §2 row menu and §6 create/edit; 3 = §4 DataTable lists and §5 sub-pages (including the document page tabs). §8 pickers, §11 navigation (except labels) and §12 badges are out of scope.

---

# Admin UI: design and UX consistency audit

Scope: every route in `services/admin-ui/app/routes.ts` (code at `services/admin-ui/app`). Read-only audit; nothing was changed.
Paths below are relative to `services/admin-ui/app/`. Groups are ranked by how visible and annoying they are, worst first.

---

## 1. Page header: wrong title, double headings, six back-link styles (worst: seen on every detail page)

### Findings
- **"Administration" with an empty description appears above the real header** on `/services/:id/*`, `/services/new` and `/environments/:id`. `AdminLayout` matches only the exact list paths (`services = pathname === "/services"`, `environments = pathname === "/environments"`, `components/AdminLayout.tsx:88-89`). These pages fall through to `"Administration"` / `""` (`AdminLayout.tsx:147,190`), so the service page shows the h1 "Administration", then its own `h2` name (`features/servers/ServiceLayout.tsx:190`). The environment page shows **two h1s**: "Administration" plus the environment name (`features/servers/EnvironmentPage.tsx:87`). On the same pages the sidebar highlights nothing (`AdminLayout.tsx:346,354`).
- **Two h1s** on `/documents/new` ("Documents" from the layout plus `h1` "New document", `features/documents/NewDocumentLayout.tsx:15`) and on `/documents/drafts/:id` ("Documents" plus `h1` "Draft…", `features/documents/DraftPageView.tsx:31`).
- **Only some detail pages hide the layout heading.** `/sessions/:id` and `/servers/:id` hide it (`AdminLayout.tsx:98`). `/agents/:id`, `/services/:id`, `/code-projects/:id`, `/skills/:id`, `/output-schemas/:id` and `/prompts/:id` show "Agents"/"Skills"/… above their own name.
- **Heading size and level of the record name differ:** `h2 text-xl` on agent, service, code project and output schema (`AgentLayout.tsx:82`, `ServiceLayout.tsx:190`, `CodeProjectLayout.tsx:126`, `OutputSchemaEditorView.tsx:189`); `h2 text-lg` on skill and run record (`SkillEditor.tsx:236`, `RunRecordView.tsx:124`); `h1 text-2xl` on environment, server, session and draft (`EnvironmentPage.tsx:87`, `ServerPage.tsx:63`, `SessionView.tsx:124`, `DraftPageView.tsx:31`).
- **Back link: six styles.**
  - `Button variant="link"` + ArrowLeft, "All agents" / "All services" / "All code projects" (`AgentLayout.tsx:59-64`, `ServiceLayout.tsx:92-98`, `CodeProjectLayout.tsx:103-110`). This is the target.
  - Muted text link + small ArrowLeft, "All skills" (`SkillEditor.tsx:231-234`).
  - `Button variant="ghost"` + ArrowLeft with the bare plural "Documents" / "Prompts" (`DocumentEditorView.tsx:449-454`, `DraftPageView.tsx:25-29`, `DocumentationPromptsView.tsx:247-252`).
  - Ghost button with no icon, "All schemas", placed **on the right** among the actions (`OutputSchemaEditorView.tsx:197-199`).
  - Plain underlined text, "Environments" / "Servers" (`EnvironmentPage.tsx:52-56`, `ServerPage.tsx:58-60`).
  - **No back link** on `/sessions/:id` (`SessionView.tsx`), `/activity/runs/:runKey` (`RunRecordView.tsx:121-135`) or `/documents/new`.
- **Primary action placement:** list pages put it top right (good) but row it differently. Some pair it with a count line on the left ("N services · Application assets", `ServicesView.tsx:98-103`; "N servers · Application infrastructure", `ServersView.tsx:118-121`). Others use a bare right-aligned button (`OutputSchemasView.tsx:31`, `SchedulesView.tsx:178`, `HandlersView.tsx:98`). Agents puts a search box on the left (`AgentsView.tsx:128-141`). Prompts repeats the layout description as the left text (`DocumentationPromptsView.tsx:73-75` duplicates `AdminLayout.tsx:151`). The MCP count reads "0 connections" while loading (`MCPServersView.tsx:77`).
- **Primary action verb:** "New …" (agent, skill, project, service, document, event, handler, schedule, entry, schema), "Add …" (server `ServersView.tsx:136`, model `LlmSettingsView.tsx:95`, MCP server `MCPServersView.tsx:85`, environment `EnvironmentsView.tsx:124`), "Create prompt" (`DocumentationPromptsView.tsx:79`). The server sheet opened by "Add server" is titled "Register server" (`ServerForm.tsx:65`).
- **Icons in buttons:** most use `data-icon="inline-start"`; `LlmSettingsView.tsx:84,94` omit it; `AgentsView.tsx:234` puts `data-icon="inline-start"` on an icon-only button. Header buttons are `size="sm"` on resource pages (`ResourceChats.tsx:104`, `CodeProjectLayout.tsx:170`, `SessionView.tsx:142`) and default size on list pages and the editor pages.

### Standard
`AdminLayout` decides the title by section prefix (`startsWith("/services")`, `"/environments"`, …), so the sidebar always highlights the section. A detail page shows the section title in the layout. The record has one header block: the back link `Button variant="link"` + ArrowLeft + "All <plural>" (export the `AllServices` pattern as a shared `BackLink` in `components/`), then `h2 text-xl` name + badges, with actions top right. Remove the page-level `h1`s (NewDocumentLayout, DraftPageView, EnvironmentPage, ServerPage, SessionView) or make the layout skip its heading for them consistently. Primary create verb: **"New <thing>"**.

**Size: small** (AdminLayout prefixes + a BackLink component). Touches: AdminLayout, `/services/:id/*`, `/services/new`, `/environments/:id`, `/servers/:id`, `/skills/:id`, `/output-schemas/:id`, `/prompts/:id`, `/documents/:id`, `/documents/new`, `/documents/drafts/:id`, `/sessions/:id`, `/activity/runs/:runKey`, plus the "Add"/"Create" buttons on servers, models, MCP servers, environments and prompts.

---

## 2. Row actions: five patterns, only `/skills` follows the convention

### Findings
- **"⋯" DropdownMenu (target):** `/skills` only (`features/agents/SkillsView.tsx:293-356`); `AgentActions` uses it in the agent header (`AgentActions.tsx:152-161`).
- **Inline icon buttons with tooltips (`RowAction`):** `/services` has 3 (`ServicesView.tsx:224-244`), `/servers` has 5 (SSH settings, Test SSH, Documents, Edit, Delete, `ServersView.tsx:262-307`), `/environments` has 5 plus 2 draft icons (`EnvironmentsView.tsx:215-260`). The pattern recurs in service sub-tabs, the environment workspace, `ServerPage.tsx:122-129` and `EntryDocuments`.
- **Inline icon buttons without tooltips:** `/agents` has Run, Open, Clone and Remove (`AgentsView.tsx:199-235`). Open duplicates the name link.
- **Ghost text + icon buttons:** `/code-projects` has "Sync now" and "Delete" (`CodeProjectsList.tsx:218-246`), `/schedules` has "Run now", "Edit", "History" and "Delete" (`SchedulesView.tsx:262-297`), `/events/handlers` has "Edit" and "Delete" (`HandlersView.tsx:168-196`).
- **Outline text buttons:** `/mcp-servers` has "Test connection", "Edit" and a **filled destructive "Remove"** in every row (`MCPServersView.tsx:303-325`). `/llm-settings` has "Test connection" and "Edit" (`LlmSettingsView.tsx:172-193`). The `/documents?view=drafts` rows have "Open", "Publish" and "Discard" (`DocumentsView.tsx:287-312`). `/code-graphs` has "Explore" and "Generate" (`CodeGraphsView.tsx:144-161`).
- **Whole row clickable, no actions column:** `/events` and `/notifications` (`EventsView.tsx:393-400`, `NotificationsView.tsx:205-210`). Elsewhere the name is a link.
- **Name link styles differ:** `underline` always (services, code projects, environments), `hover:underline` (agents, skills, documents, schemas), plain button (memory, `MemoryView.tsx:244`). MCP and LLM rows have no link (`MCPServersView.tsx:240`, `LlmSettingsView.tsx:155`).

### Standard
One `RowMenu` component in `components/` (lift `SkillActions`): a ghost icon "⋯" trigger, with items as icon + label. Delete goes last, after a separator, in destructive text, and always opens the AlertDialog. Keep at most **one** inline quick action when it is the main job of the row (Run on agents, Sync now on code projects, Run now on schedules, Test on MCP/LLM, Confirm on drafts); everything else goes in the menu. Drop "Open"/"Details" items that duplicate the name link. Events and notifications can keep the clickable row that opens a Sheet, since that row is read-only.

**Size: medium** (mechanical, one shared component). Touches: `/agents`, `/code-projects`, `/services`, `/servers`, `/environments`, `/mcp-servers`, `/llm-settings`, `/schedules`, `/events/handlers`, `/documents` (drafts tab), `/code-graphs`, `/servers/:id`, the service tabs (Code, relations, Connections, Documents) and the environment workspace rows.

---

## 3. Removals: unconfirmed deletes, Delete/Remove mixed in one flow, confirm buttons not always destructive

### Findings
- **Unconfirmed removals** (a click deletes at once):
  - Environment workspace entry sheet (`/environments/:id`): Remove connection (`features/servers/EntrySheet.tsx:534-547`), Remove relation (`EntrySheet.tsx:612-623`), Remove document link (`EntrySheet.tsx:672-679`).
  - Document page (`/documents/:id`, Connections tab): Remove depends-on link (`features/documents/DocumentConnections.tsx:86-95`).
- **Delete and Remove within the same flow:** on `/code-projects` the row button says "Delete" (`CodeProjectsList.tsx:244`), the dialog says "Remove code project?" (`:336`) and the action says "Remove project" (`:375`). On `/agents` the dialog says "Remove agent?" but the history variant says "Delete agent and history" (`AgentsView.tsx:258,289,299`), and the toast says "removed" or "deleted" depending on the case (`:94`).
- **Confirm button variant:** destructive on skills, services, servers, environments, output schemas, memory, clones and `ConfirmRemove`. **Default (not red)** on agents "Remove agent" (`AgentsView.tsx:292`), code projects "Remove project" (`CodeProjectsList.tsx:369`), MCP "Remove MCP server" (`MCPServersView.tsx:355`), schedules "Delete" (`SchedulesView.tsx:351-360`), handlers "Delete" (`HandlersView.tsx:223-232`), and documents "Discard draft" (`DocumentsView.tsx:477-486`).
- **Action labels:** "Delete <thing>" on skills, services, servers, environments, schemas, memory and documents. A bare "Delete" on schedules and handlers. "Remove" in `ConfirmRemove` (`ServiceLayout.tsx:151`). "Remove <thing>" on agents, code projects and MCP.
- **Title formats:** `Delete “name”?` (services, servers, environments, documents), `Delete name?` (skills, schemas, memory), `Delete schedule name?` / `Delete handler name?`, `Remove agent?` / `Remove code project?` / `Remove MCP server?` (no name).
- **Inventory draft discard opens a Sheet** (`ConfirmDraftSheet`). Document draft discard opens an AlertDialog. Both work, but the same act looks different.
- `ConfirmRemove` lives in `features/servers/ConfirmRemove.tsx` and is used by only 4 files (ServiceLayout, ServicePanel, EnvironmentWorkspace, CodeProjectDependencies). The other ~14 removal dialogs hand-roll the same AlertDialog.
- No browser `confirm()` anywhere. The `confirm(...)` calls in `DraftView.tsx:262,273` and `AgentDraftBar.tsx:105` are a local unsaved-changes helper. Good.

### Standard
Move `ConfirmRemove` to `components/` and use it for every removal. Extend it with an optional `children` slot (agents' history note, code projects' "also delete folders" checkbox) and a `busy`/`error` state. Wording:
- **"Delete"** when the record itself is destroyed: title `Delete “<name>”?`, action `Delete <thing>`.
- **"Remove"** only when a link or membership goes and both records stay.
- **"Discard"** only for drafts and unsaved edits.

The confirm button is always destructive. Wire the four unconfirmed removals through it.

**Size: medium.** Touches: `/environments/:id`, `/documents/:id`, `/agents`, `/code-projects`, `/mcp-servers`, `/schedules`, `/events/handlers`, `/documents` (drafts), plus a mechanical swap on `/skills`, `/services`, `/servers`, `/environments`, `/output-schemas/:id`, `/memory`, `/prompts/:id` and `/documents/:id`.

---

## 4. Long lists: `DataTable` is used once; `/agents` does not use it; three home-made pagers

### Findings
- **`DataTable` is used only on `/code-projects`** (`features/codeProjects/CodeProjectsList.tsx:308`). **`/agents` uses a plain `Table`** with no pagination or sorting (`AgentsView.tsx:161-248`), contrary to the stated convention.
- **Hand-rolled copies of DataTable:**
  - `/activity/runs`: server-side paging written from scratch. It has **native `<select>`s** for five filters and page size (`features/sessions/RunsView.tsx:30,94-170,265-275`), page sizes 25/50/100 against DataTable's 10/20/50/100, "Previous"/"Next" text buttons, and "↑/↓" text sort marks (`RunsView.tsx:80-83`) where code projects and events use ArrowUpDown icons.
  - `/documents`: offset paging with a fixed 50 rows and Previous/Next (`DocumentsView.tsx:441-466`).
  - `/memory`, `/events`, `/notifications`: "Load more" (`MemoryView.tsx:276-281`, `EventsView.tsx:420-425`, `NotificationsView.tsx:225-230`).
- **No paging, sorting or search at all:** `/skills`, `/services`, `/servers`, `/environments` (client-side search only), `/output-schemas` (no search, `OutputSchemasView.tsx:59-86`), `/prompts` (tag Select only), `/mcp-servers`, `/llm-settings`, `/schedules`, `/events/handlers`, `/code-graphs`.
- **Search field style:** an unlabelled `Input` with a placeholder ("Find an agent…" `AgentsView.tsx:129-135`, "Filter by …" on services, servers and environments). A labelled `Field` ("Filter skills" `SkillsView.tsx:193-201`, "Search documents" `DocumentsView.tsx:181-189`, "Ticket or title" `EventsView.tsx:326-329`). `type="search"` (code projects only). Wording varies between Find, Filter and Search.
- **Table frame:** `rounded-lg border` on most pages and `rounded-md border` on others. **No border at all** on `/mcp-servers` (`MCPServersView.tsx:231`) and `/schedules` (`SchedulesView.tsx:203`). LLM wraps its table in a Card titled "Model connections" (`LlmSettingsView.tsx:124-130`).
- **Empty states:**
  - `Empty` with icon, title and description on most pages.
  - **No icon** on MCP (`MCPServersView.tsx:221-227`), prompts (`DocumentationPromptsView.tsx:110-128`), code graphs, storage, and the code project "no match" state.
  - **Plain text** on `/llm-settings` (`LlmSettingsView.tsx:133-135`) and `/documents?view=drafts` (`DocumentsView.tsx:250`).
  - **The EmptyTitle just repeats the page name** on `/schedules`, `/events/handlers`, `/events`, `/notifications`, `/memory` and `/output-schemas` (`SchedulesView.tsx:197`, `HandlersView.tsx:118`, `EventsView.tsx:352`, `NotificationsView.tsx:182`, `MemoryView.tsx:219`, `OutputSchemasView.tsx:51`). Services and servers use an action title ("Build your service inventory").
  - A first-item CTA appears only on services, servers, environments and Jira tickets.
  - The "no match" row is styled `h-24 text-muted-foreground` on agents and DataTable, but plain on skills, services, servers and environments (`SkillsView.tsx:256`, `ServicesView.tsx:250`).
- **Loading:**
  - A `Skeleton` on most pages.
  - "Loading…" text inside the DataTable empty cell (`components/DataTable.tsx:77`).
  - A spinner and "Loading model settings…" on LLM (`LlmSettingsView.tsx:117-121`).
  - "Loading tools…" (`ToolsList.tsx:46`) and "Loading folders…" (`CodeFolderTree.tsx:111`).
  - "Loading inventory…" or "Loading…" in the count line (`ServicesView.tsx:101`, `ServersView.tsx:119`, `CodeGraphsView.tsx:71`).
- **Refresh button** on skills, services, servers, environments, documents, MCP, LLM, memory, events, notifications, code graphs and storage. Not on agents, code projects (it polls), output schemas, prompts, schedules, handlers or runs.

### Standard
`DataTable` for every record list. First add to it:
- a `sortHeader(label, key)` helper (lift it from `CodeProjectsList.tsx:144-152`)
- a standard toolbar slot: one `type="search"` Input, "Search <plural>", unlabelled with an aria-label, plus Select filters
- a Skeleton row for loading
- an `Empty` element for empty

Client-side lists slice like code projects do. Server-side lists (runs, documents, memory, events, notifications) pass `total`. Drop "Load more" and the custom pagers. Empty state: `Empty` with icon, an action title ("No schedules yet") and the primary CTA. Keep Refresh only where data changes on its own (runs, events, notifications, memory, code graphs); polling pages don't need it.

**Size: large** (≈15 pages; runs, documents and the three "Load more" lists need paging changes). Touches: `/agents`, `/skills`, `/services`, `/servers`, `/environments`, `/output-schemas`, `/prompts`, `/mcp-servers`, `/llm-settings`, `/schedules`, `/events`, `/events/handlers`, `/notifications`, `/memory`, `/activity/runs`, `/documents`, `/code-graphs`, `/jira/tickets`.

---

## 5. Sub-pages: `?view=` / `?tab=` switches remain, stacked sections, and most tabs have no description

### Findings
- **`?view=` tab switches (not routes):**
  - `/servers/:id`: Overview / Documents via `ResourceChats pages` (`features/servers/ServerPage.tsx:144-161`, `ResourceChats.tsx:89-101`).
  - `/environments/:id`: Workspace / Documents (`EnvironmentPage.tsx:108-135`). The header even builds `?view=documents` links (`:75-76`).
  - `/documents`: Documents / Drafts via `?view=drafts` (`DocumentsView.tsx:71,150-161`).
  - `/documents/:id`: 7 in-page tabs (Write, Preview, Linked resources, History, Drafts, Connections, Agent runs) kept in `?tab=` (`DocumentEditorView.tsx:206-213,654-679`). Other pages link to `?tab=drafts` (`DocumentsView.tsx:420`).
- **`/events` and `/events/handlers`** are separate top-level routes switched with shadcn `Tabs` and `navigate` (`features/events/EventsPage.tsx:10-21`), not a parent route with `SectionLinks`. It looks almost the same but uses a different component.
- **Stacked sections instead of sub-pages or sheets:**
  - `/environments`: clicking "Details" opens a Card **below the table** (`EnvironmentsView.tsx:280-339`). It duplicates `/environments/:id`.
  - `/code-graphs`: "Explore" opens the graph explorer below the table, with a "Close graph" button (`CodeGraphsView.tsx:148,175-179`).
  - `/storage`: two stacked Cards, overview and file browser (`components/StorageOverviewView.tsx:139,224`). The file browser duplicates `/code-projects/folder`.
  - `/schedules`: History expands an extra table row (`SchedulesView.tsx:298-318`).
  - `/environments`: Add/Edit **replaces the list** with an inline form (`EnvironmentsView.tsx:140,156`).
- **One-line tab descriptions** exist only on the service tabs (`ServiceLayout.tsx:32-43,210`). They are missing on:
  - the agent tabs (`AgentLayout.tsx:22-26,91-94`)
  - the code project tabs (`CodeProjectLayout.tsx:28-33`); Dependencies has descriptions inside its cards (`CodeProjectDependencies.tsx:87`), not under the tab links
  - `/code-projects` vs `/code-projects/folder` (`CodeProjectsLayout.tsx:18-21`)
  - `/documents/new` Write vs Write with an agent (`NewDocumentLayout.tsx:16-22`)
  - the server, environment and events tabs

### Standard
Every multi-part page is a parent route with `SectionLinks` and an `Outlet`, followed by a `SECTION_HELP` line. Make `SectionLinks` take an optional `help` per section and render the line itself, so no page can forget it. Convert:
- `/servers/:id` → `overview|documents`
- `/environments/:id` → `workspace|documents`
- `/documents` → `/documents` + `/documents/drafts`
- `/events` → a parent route with `SectionLinks`

Keep a redirect from the old `?view=` like `ServiceIndex` does. For `/documents/:id`, at least move History, Drafts, Connections and Agent runs to sub-pages and keep Write/Preview as the in-page editor toggle. Replace the environment Details card with the link to `/environments/:id`, and put the graph explorer on its own route or in a Sheet.

**Size: large** (documents detail is the biggest piece; servers, environments, documents list and events are medium each; the descriptions are small). Touches: `/servers/:id`, `/environments/:id`, `/environments`, `/documents`, `/documents/:id`, `/events`, `/events/handlers`, `/code-graphs`, `/storage`, `/agents/:id/*`, `/code-projects/:id/*`, `/code-projects(/folder)`, `/documents/new(/agent)`.

---

## 6. Create and edit: inline Cards, Sheets and pages, with no rule for which

### Findings
- **Inline Card on the list page:**
  - `/agents` "New agent" opens a "New Agent Wizard" card (Title Case) above the table (`AgentsView.tsx:143-147`, `AgentWizard.tsx:238-240`).
  - `/mcp-servers` add/edit (`MCPServersView.tsx:100-216`).
  - `/llm-settings` add/edit (`LlmSettingsView.tsx:110-115`, `LlmModelForm.tsx:84`).
  - `/environments` add/edit replaces the list (`EnvironmentsView.tsx:140-156`, `EnvironmentForm.tsx:50`).
- **Right Sheet:** new code project, new/import skill, add/edit server, SSH settings, new/edit schedule, new/edit handler, new event, memory entry, and the environment workspace adds. The workspace files are named `Add*Dialog.tsx` but render Sheets; the codebase has no Dialog component at all.
- **Full page:** `/services/new`, `/documents/new`, `/prompts/new`, `/output-schemas/new`. Editing a service is a tab (`/services/:id/edit`), editing a server is a Sheet, editing an environment is an inline form.
- **Save / cancel labels:**
  - Save labels: "Save", "Save MCP server" (`MCPServersView.tsx:209`), "Save settings" (`ServerSSHForm.tsx:267`), "Save project" / "Save and clone" (`CodeProjectForm.tsx:355`), "Save prompt" / "Create prompt" (`DocumentationPromptsView.tsx:270`), "Save resources" (`AgentResources.tsx:131`), "Save links" (`DocumentEditorView.tsx:725`).
  - Cancel labels: "Cancel" (most), "Close" (`ServerSSHForm.tsx:286`), "Done" (`AgentSettingsSheet.tsx:333`, `SuggestionApply.tsx:195`), "Discard" (in-place edit), "Discard changes" / "Discard prompt changes" (agent cards).
- **Save placement:**
  - Header top right on the editor pages: document, prompt, skill (`DocumentEditorView.tsx:504-512`, `DocumentationPromptsView.tsx:268`, `SkillEditor.tsx:253`).
  - Bottom left on the forms: service edit, code project settings, sheets.
  - Output schema: Save bottom left with a destructive Delete bottom right (`OutputSchemaEditorView.tsx:279-290`). It is the only editor with Delete at the bottom; the document and prompt editors put Delete in the header as an outline button.

### Standard
Per `ui-lists-in-side-sheets`: **create and edit of a small record goes in a right Sheet** (MCP server, model, environment, agent wizard, server). Records with sub-pages are edited on their Edit/Settings sub-page. Long documents (document, prompt, skill, output schema) get a full page with Save in the header, top right. Buttons: the primary is "Save" (or "Create <thing>" in a create sheet). The secondary is "Cancel" in a Sheet and "Discard" in in-place edit forms. Delete goes in the page header's "⋯" menu, never as a footer button.

**Size: medium.** Touches: `/agents`, `/mcp-servers`, `/llm-settings`, `/environments`, `/output-schemas/:id`, `/servers` (labels), `/code-projects` (labels), plus renaming the `Add*Dialog.tsx` files (developer-facing).

---

## 7. Unsaved edits: three behaviours (LeaveGuard, a second "Keep editing" dialog, nothing)

### Findings
- **`LeaveGuard`** (Leave / Stay) on `/agents/:id/general` and `/resources` (`AgentPages.tsx:40,52`), `/services/:id/edit` (`ServiceEdit.tsx:123`) and `/code-projects/:id/settings` (`CodeProjectPages.tsx:181`).
- **A second implementation, `UnsavedDocumentChanges`** ("Discard unsaved changes?", Keep editing / Discard changes, `features/documents/UnsavedDocumentChanges.tsx:42-60`), used by `/documents/:id` and `/documents/new` (`DocumentEditorView.tsx:447`), `/prompts/:id` (`DocumentationPromptsView.tsx:245`) and `/skills/:id` (`SkillEditor.tsx:483`). Its only real difference is that it ignores `/agent/:runId` path changes.
- **No guard** although edits can be lost:
  - `/output-schemas/:id` (no dirty tracking at all)
  - `/jira/settings` (computes `dirty`, `JiraSettingsView.tsx:50`, but never guards)
  - the inline forms on `/mcp-servers`, `/llm-settings` and `/environments`
  - every Sheet: closing the handler, schedule, memory, server or event sheet drops edits silently

### Standard
One `LeaveGuard` with an optional `samePage(pathA, pathB)` predicate to cover the document `/agent/:runId` case; delete `UnsavedDocumentChanges`. Labels: **Stay / Leave**. Add it to the output schema editor and Jira settings. For Sheets, add a small `useSheetGuard(dirty)` that asks the same question when a dirty sheet is closed.

**Size: small** (pages) **to medium** (sheets). Touches: `/documents/:id`, `/documents/new`, `/prompts/:id`, `/skills/:id`, `/output-schemas/:id`, `/jira/settings`, `/mcp-servers`, `/llm-settings`, `/environments`, and the Sheets on `/schedules`, `/events/handlers`, `/events`, `/memory` and `/servers`.

---

## 8. Pickers: the PickSheet convention is used in 3 places; Selects pick records elsewhere

### Findings
- **`PickSheet`** (right Sheet with search, the target) is used on `/services/:id/code` (`ServiceCode.tsx:9`), the service relation tabs (`ServiceRelations.tsx:7`) and `/code-projects/:id/dependencies` (`CodeProjectDependencies.tsx:16`).
- **A `Select` inside a Sheet to choose a record:**
  - Environment workspace (`/environments/:id`): "Choose a server" (`AddServerDialog.tsx`), "Choose a service" (`AddServiceDialog.tsx`), "Choose a server" in `AddDeploymentSheet.tsx`, "Choose a deployment" / "what it connects to" in `AddConnectionDialog.tsx`, and the relation target (code project or service) in `AddRelationDialog.tsx` and `EntrySheet.tsx`.
  - Agents: "Choose an agent" in `JiraUserAgents.tsx` (`/jira/settings`) and `HandlerSheet.tsx` (`/events/handlers`).
  - Others: the schedule target (`NewScheduleSheet.tsx`), the output schema (`OutputSchemaPicker.tsx`), and models (`AgentWizard.tsx`, `AgentSettingsSheet.tsx`, `EmbeddingModelSelect.tsx`).
- **Checkbox groups without search:** document linked resources (`components/ResourcePicker.tsx`, via `DocumentResourceSelection.tsx`) on `/documents/:id` and `/documents/new`.
- **Switch list with a filter in tabs:** agent resources (`ResourceTabs.tsx`). This is a reasonable multi-select variant.
- Filters on `/documents` (`DocumentsView.tsx:199-219`) and `/memory` use a Select over every service, server, code project and agent. That is fine for a filter, but it gets long.

### Standard
**Choosing one inventory record** (server, service, deployment, code project, agent) uses `PickSheet` (move it to `components/`). Short fixed choices (environment kind, type, role, relation kind, model, schedule target) stay a `Select`. Multi-select of records uses `ResourceTabs` (switches with a filter), and that should replace `ResourcePicker`'s bare checkboxes.

**Size: medium.** Touches: `/environments/:id` (5 sheets), `/events/handlers`, `/jira/settings`, `/documents/:id`, `/documents/new`.

---

## 9. Feedback after an action: toasts on agent pages, inline Alerts elsewhere, silence on several pages

### Findings
- **Toast (sonner, mounted in `AdminLayout.tsx:442`)** only on agent pages: remove/clone (`AgentsView.tsx:94,114`), wizard (`AgentWizard.tsx:229`), saves (`AgentProfileSettings.tsx:77`, `AgentResources.tsx:99`), clone actions (`AgentActions.tsx:71,122`).
- **Inline `Alert role="status"` notice** on `/code-projects` (`CodeProjectsLayout.tsx:48-52`), the code project page, `/skills` and the skill editor, `/services` and the service page, `/servers` (plus a separate SSH test Alert, `ServersView.tsx:151-161`), `/environments`, `/mcp-servers`, `/llm-settings`, `/code-graphs`, `/documents` ("published"), `/documents/drafts/:id` and `/jira/settings`. These notices stay until another action and push the page down.
- **No feedback at all** after:
  - save, toggle, Run now or delete on `/schedules`, and save, toggle or delete on `/events/handlers`
  - save on `/output-schemas/:id`, `/memory` (sheet) and `/prompts/:id`
- **Error Alert title:** none (most), the page name ("Schedules", "Handlers", "Events", "Notifications", "Memory": `SchedulesView.tsx:185`, `HandlersView.tsx:106`, `MemoryView.tsx:209`), or a descriptive one ("Service inventory unavailable", `ServicesView.tsx:119`). There are three retry styles: an outline button (`AgentsView.tsx:152`), a link button (`DocumentationPromptsView.tsx:87`, `JiraTicketsView`), or none.

### Standard
**Success → `toast.success`** (transient, already used on the agents pages, and the Toaster is global). **Errors and conflicts that need reading → an inline destructive `Alert`** near the form or list, with no title (or a descriptive one, never the page name) and a "Retry" button where a reload helps. Test-connection results can stay inline next to the row (they carry details).

**Size: small–medium** (mostly replacing `setNotice` with `toast.success`). Touches: `/code-projects(/:id)`, `/skills(/:id)`, `/services(/:id)`, `/servers`, `/environments`, `/mcp-servers`, `/llm-settings`, `/code-graphs`, `/documents`, `/documents/drafts/:id`, `/jira/settings`, `/schedules`, `/events/handlers`, `/output-schemas/:id`, `/memory`, `/prompts/:id`.

---

## 10. Terminology

### Findings
- **Making a draft live** has three verbs: "Confirm" for inventory drafts (`ConfirmDraftSheet.tsx:52`), "Publish" for new documents (`DocumentsView.tsx:310`, `DocumentEditorView.tsx:503`), and "Promote" for drafts of existing documents (`DraftView.tsx:279`, with "Promote anyway"). The convention says Confirm / Discard.
- **Draft badges:**
  - "Draft" (`DraftBadge`, outline)
  - "Draft — not published" (`EntryDocuments.tsx:91,119`), plus an Alert reading "Draft — not published yet" on the document page (`DocumentEditorView.tsx:521`)
  - "Draft waiting" (`EntryDocuments.tsx:120`)
  - "Not published" (`DocumentsView.tsx:277`)
  - "New document" / "New version" (`EnvironmentDocumentDrafts.tsx:36`)
  - lowercase destructive "stale" (`DocumentsView.tsx:272`, `DraftView.tsx:260`)
  - On prompts, "Draft" means an unactivated version (`DocumentationPromptsView.tsx:74,165,358`), a different concept under the same word.
- **Run / session / chat / conversation:**
  - The sidebar says "Agent runs" (`/activity/runs`).
  - The agent tab is "Chat"; the chat panel says "Conversations" and "New chat" (`ChatColumn.tsx:109`).
  - `/sessions/:id` has a "Close session" button and says "This session is closed. Start a new session to continue." (`SessionView.tsx:142-144`). `AgentChat` says "Close this conversation?" and "This conversation is closed. Start a New chat." (`AgentChat.tsx:548,595`).
  - The session page shows "Previous output from run …" linking to a `/sessions/` URL (`SessionView.tsx:130-135`), and the document page has an "Agent runs" tab listing chats.
- **Code project / project / repository:** the sidebar says "Projects" (`AdminLayout.tsx:386`), the page title "Code projects", the list button "New project" (`CodeProjectsLayout.tsx:44`) and the sheet "New code project". The services filter placeholder says "repository" (`ServicesView.tsx:155`), the services column "Source" lists code projects (`ServicesView.tsx:164`), and the code graphs column says "Project".
- **New / Add / Create / Register:** see group 1. **Delete / Remove:** see group 3.
- **On / Enabled / Allowed:** the servers column "SSH allowed" shows "On"/"Off" (`ServersView.tsx:257-258`), the server page shows "Agent SSH allowed"/"Agent SSH off" (`ServerPage.tsx:77-78`), and agents, prompts and models use "Enabled"/"Disabled". The schedules and handlers switch column is headed "On"; skills uses "Enabled".

### Standard
- **Confirm / Discard** for every draft, documents included (rename Publish and Promote to "Confirm"; keep "Confirm anyway" for the stale case). One `DraftBadge` reading "Draft", plus one `StaleBadge` reading "Stale".
- Rename the prompt version state to "Inactive version" to free "draft".
- **"Chat"** is the user-facing word for a conversation with an agent (tab, panel, close, new). "Run" is reserved for the execution records under Agent runs. Rename "Close session" → "Close chat".
- **"Code project"** everywhere: sidebar, buttons, column headers.
- **"Enabled / Disabled"** for every on/off state.

**Size: small–medium** (strings, plus a few test updates). Touches: `/documents`, `/documents/:id`, `/documents/drafts/:id`, `/servers/:id`, `/services/:id/documents`, `/environments/:id`, `/prompts`, `/sessions/:id`, `/agents/:id/chat`, `/code-projects`, `/services`, `/code-graphs`, `/servers`, `/schedules`, `/events/handlers`.

---

## 11. Navigation: sidebar groups and labels versus page titles

### Findings
- **Labels that differ from page titles:** "Projects" vs "Code projects"; "Models" (group "LLM Settings") vs "LLM Settings"; "Tickets"/"Settings" vs "Jira Tickets"/"Jira Settings"; "All agents" vs "Agents" (`AdminLayout.tsx:294,315,328,336,386`). Title Case appears in "LLM Settings", "Jira Settings" and "Jira Tickets"; everything else is sentence case.
- **Group "Administration"** mixes:
  - agent building blocks (Skills, Prompts, MCP servers)
  - activity (Agent runs, Schedules, Events, Notifications)
  - Memory and Storage (`AdminLayout.tsx:210-286`)

  Meanwhile "Agents" holds only All agents and Output schemas. The "Environments" group holds Services, Servers and System map. "Docs" and "LLM Settings" hold one item each.
- **Duplicate icons:** BookOpen for both Skills and Prompts (`:224,232`), Network for both Environments and Code graphs (`:348,393`), Settings for both Models and Jira Settings (`:314,335`).
- `/code-projects/folder` and the `/storage` file browser are two browsers of the same code root.
- The MCP page description "Manage external tools for Code search and SSH agents." (`AdminLayout.tsx:169`) and the MCP empty state reflect the old agent model.

### Standard
Group by what the user manages:
- **Agents:** Agents, Skills, Prompts, MCP servers, Output schemas, Memory
- **Activity:** Agent runs, Schedules, Events, Notifications
- **Inventory:** Environments, Services, Servers, System map
- **Code:** Code projects, Code graphs, Storage
- **Docs:** Documents
- **Settings:** Models, Jira

Sidebar label = page title, in sentence case: "LLM settings", "Jira tickets", "Jira settings". Give each item its own icon.

**Size: small.** Touches: `components/AdminLayout.tsx` only (plus tests).

---

## 12. Badges, status colours and dates

### Findings
- Status colours are mostly consistent: `secondary` for normal or OK, `destructive` for failed, `outline` for off, pending or draft (EventsView `StatusBadge` `:56-60`, NotificationsView `:33-37`, ScheduleStatus `:78-82`, handlers last run, code project sync and graph). Exceptions:
  - `/activity/runs` shows the status as **plain lowercase text** ("working", `RunsView.tsx:245`).
  - `/activity/runs/:runKey` shows the status as a **filled default Badge** and the kind as a raw identifier (`RunRecordView.tsx:126-129`).
  - "stale" is a lowercase destructive badge (see group 10).
- **Enabled state shown two ways:** a Badge (agents, prompts, models) or an in-row Switch (skills, MCP, schedules, handlers).
- **Dates:** all use `new Date(x).toLocaleString()` (consistent). The same `when` helper is redefined in 11 files (for example `CodeProjectsList.tsx:65`, `CodeProjectLayout.tsx:34`, `MemoryView.tsx:29`). There are no relative dates anywhere.

### Standard
Add one `StatusBadge` in `components/` with a status-to-variant map shared by runs, events, notifications, schedules, handlers and code project jobs, with labels in sentence case. One `formatDate` in `lib/`. Pick one enabled control per list: a Switch where toggling in place is safe (skills, MCP, schedules, handlers, and also models and prompts), a Badge on agents only if toggling needs a check.

**Size: small.** Touches: `/activity/runs`, `/activity/runs/:runKey`, `/documents`, `/documents/drafts/:id`, `/llm-settings`, `/prompts`, plus the date helper refactor (no visual change).

---

## Already consistent (leave alone)

- **Shell:** `AdminLayout` with sidebar, theme toggle, global `Toaster` and the `max-w-6xl` content width with `useWidePage` for chat layouts.
- **Routed sub-pages with `SectionLinks`** on agents, services, code projects (detail and list/folder) and new document, each with an index that redirects old `?view=` links (`AgentPages.tsx:10-17`, `ServiceIndex.tsx:3-10`, `CodeProjectPages.tsx:15-22`).
- **Service tabs:** counts in the labels and a `SECTION_HELP` line (the model for every tabbed page).
- **`LeaveGuard`** on agent general/resources, service edit and code project settings.
- **Resource pages** (service, code project, server, environment) share `ResourceChats`: Chat, Discover…, and Hide chat.
- **`PickSheet`** for service Code, relations and code project dependencies; **`ConfirmRemove`** on service relations, the environment workspace and code project dependencies.
- **Inventory drafts:** `DraftBadge` with Confirm/Discard via `ConfirmDraftSheet` across services, servers, environments and the workspace.
- **No browser `confirm()`**; every record deletion except the four listed in group 3 is confirmed with an AlertDialog.
- **Badge colour semantics** (secondary / destructive / outline) and **date format** (`toLocaleString`) are uniform.
- **`/skills`** row "⋯" menu, and **`/code-projects`** DataTable with search, filters and sortable headers: these are the reference implementations.
