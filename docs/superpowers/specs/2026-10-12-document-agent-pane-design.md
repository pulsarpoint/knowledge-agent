# Ask agent as a chat pane on the document page — design

Date: 2026-10-12
Status: approved in conversation. Builds on Ask agent on a document (`2026-10-12-document-agent-design.md`, merged
31ed910). UI only; the backend is unchanged.

## Why

Ask agent today is a start sheet (instruction, settings, Start) that sends the person to a separate run page. The
conversation belongs beside the document: the person keeps the editor in view, chats with the agent in a pane next to
it, and opens the settings only when they want to change what the agent may use.

## Decisions

- The document page splits into the document and a chat pane. The pane looks like the existing run conversation.
- Settings (model, resources, tools, agents, system prompt, Preview) live in a Settings sheet opened from the pane. The
  sheet never holds the chat.
- The document side keeps the editor and its tabs; an agent draft shows as a bar above the editor (Compare, Promote).
- One conversation per document at a time: the pane opens the latest open run; New chat closes it and starts fresh.
  Earlier runs open from a short list in the pane.
- Settings are locked per conversation: an open run's sheet is read-only (its saved configuration); Start new chat with
  these settings unlocks it. Changes apply only to the next conversation.

## Layout

- The header's **Ask agent** button toggles the pane. Wide screens: document about 60%, pane about 40%, a draggable
  divider. Narrow screens: the pane is a full-height sheet over the page. Whether the pane is open is remembered per
  browser (`localStorage`, guarded), so it reopens on return.
- **Document side:** the editor as today (Write, Preview, Linked resources, Version history, Drafts, Connections, Agent
  runs). When the pane's run has an open draft, a bar above the editor: "Agent draft · based on version N", a **stale**
  badge when the live version moved, **Compare** (the Drafts tab view of that draft, with its diff) and **Promote**
  (today's promote, with its stale check and confirmation).
- **Pane:** the run conversation (turns, the collapsed Worked block, tool calls, subagents and experts, Stop, the message
  box, "No draft saved in this turn"), sized for the pane. "Agents in this run" opens from a button in the pane header.
  Pane header: the run title and status, **Settings**, **New chat**, **Earlier runs**.

## Flow

- **No open run:** the pane shows an empty conversation and a one-line summary of the current settings (for example
  "GPT · web-1, Payments · 6 tools"). The first message starts a run (`POST /api/agent-sessions`) with those settings;
  the message is the `instruction`, so the run title is its first line.
- **A run is open:** messages go to it (`POST /api/generations/sessions/{id}/messages`), as follow-ups do today.
- **New chat:** closes the current run (`POST …/close`; asks first while the agent is working) and clears the pane. The
  run's draft stays as it is.
- **Unsaved editor changes:** sending the first message asks first (the existing unsaved-changes confirmation), because
  the agent works from the saved version.

## Settings sheet

- **No open run (editable):** Model; Servers and resources (the document's links ticked, unusable servers disabled with
  their reason); Tools (those that apply, all ticked; the document tools fixed); Agents (Allow subagents, experts);
  Advanced (prompt and system prompt text). **Preview** shows the JSON the next message starts with
  (`{config, first_message}`; the preview request sends the text in the message box, or `(your next message)` when the
  box is empty, since preview needs an instruction and stores nothing). **Done** closes it.
- **A run is open (read-only):** the run's saved configuration, as the fields and the raw JSON (today's Show config).
  **Start new chat with these settings** closes the run and makes the sheet editable, pre-filled from that
  configuration.
- Changes are kept while the person stays on the page and saved per document in this browser (`localStorage`,
  guarded); they are dropped and recomputed from the options when the document's links or the options no longer
  contain them. Nothing is stored on the server until a run starts; its saved configuration stays the record.

## Edge cases

- **Start refused** (disabled model, "ssh_run needs at least one server.", …): the message stays in the box, the reason
  shows above it, and the Settings button shows a dot.
- **More than one open run** (possible from the earlier version): the pane opens the latest; Earlier runs lists the
  others marked open; they still take messages. Closed runs are read-only.
- **Run closed elsewhere** (another tab, or closed after 24 hours without activity): the pane shows "This conversation
  is closed." with **New chat**.
- **Promote from the bar:** the agent's next save starts a new draft (existing tool behaviour).
- **Document deleted:** its runs go with it; the page shows its existing not-found state.

## What changes

- `AskAgentSheet` becomes the Settings sheet (Instruction and Start removed; read-only mode added).
- The run page `/documents/:documentId/agent/:runId` stays as a deep link (Agent runs tab, draft links, bookmarks): it
  shows the document page with the pane open on that run.
- The Agent runs tab stays.
- `docs/user-guide.md` "#### Ask agent" is rewritten for the pane.

## Tests (UI)

Pane toggle and remembered state; the first message starts a run with the current settings (request body pinned);
messages go to the open run; read-only sheet for an open run and Start new chat with these settings; New chat closes
the run; the draft bar with Compare and Promote, and its stale badge; a refused start keeps the message and shows the
reason; Earlier runs opens a run; the run-page deep link opens the pane on that run; unsaved editor changes are
confirmed before the first message.

## Not in scope

Backend changes; several conversations open side by side; changing a running conversation's settings; inline
suggestions in the editor; the agent editing the unsaved editor text.
