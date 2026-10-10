# Prompt library — design

Date: 2026-10-10
Status: approved in conversation. First of two specs; the second is server discovery with proposals (an SSH agent
started from a server page returns services and documents for review, plus inventory-reading agent tools).

## Why

Prompts live under Docs → Prompts and are classified only by `input_kind` (`code`, `server`, `service`, `mixed`). Server
discovery needs its own predefined, editable prompts, and more features will follow. Prompts become a library in
Administration, organised by free-text tags; a feature finds its prompts by tag.

## Data — migration 0096

- `ka.documentation_prompts` gains `tags text[] NOT NULL DEFAULT '{}'` with a CHECK: at most 10 tags, each matching
  `^[a-z0-9][a-z0-9-]{0,39}$`, no duplicates. A GIN index on `tags`.
- Backfill: every existing prompt gets `{documentation, <input_kind>}`.
- `input_kind` is retired but kept: the per-workspace seed functions (migrations 0041–0058) still insert it, so the
  column stays, gets `DEFAULT 'mixed'`, and nothing in the API or UI reads or writes it any more.
- A `BEFORE INSERT` trigger gives a prompt created with no tags the tags `{documentation, <input_kind>}`, so prompts
  seeded for new workspaces are tagged like the backfilled ones. A prompt created through the API with tags keeps
  them; through the API with no tags it gets `{documentation, mixed}` — the UI always sends the tags the user chose, so
  in practice this only matters for seeds.
- Down: drop the trigger, its function, the index and the column, and the `input_kind` default.

## API — unchanged path `/api/documentation/prompts`

- `PromptInput` loses `input_kind` and gains `tags: list[str]` (default `[]`). Tags are trimmed and lower-cased; an
  invalid tag answers 400 "Tags use lowercase letters, digits and -, up to 40 characters." and more than 10 answers
  400 "Use at most 10 tags."; duplicates are merged.
- Tags are stored on the prompt, not on a version: changing only tags (or name, description, enabled) does not create
  a new version — exactly as those fields behave today.
- `GET` list accepts `?tag=` (exact match) and every prompt row carries `tags`; `input_kind` is no longer returned.
- `GET /api/documentation/prompts/tags` returns `{"tags": [{"tag", "count"}]}` for the tag suggestions and filter,
  ordered by tag.

## UI

- Sidebar: Prompts leaves the Docs group and joins **Administration → Prompts** at `/prompts` (`/prompts/new`,
  `/prompts/:promptId`). `/documentation/prompts…` addresses redirect to the matching `/prompts…` address. Page title
  "Prompts"; description says prompts are reusable instructions for agents, organised by tags.
- List: tags as badges; a tag filter (select from the tags in use, or All).
- Form: a Tags field — type a tag and press Enter (or comma) to add it, × to remove; suggestions from the tags in use;
  the field shows the rule when a typed tag is invalid. `input_kind` select removed.
- Generate documents: the prompt picker shows `name` with its tags instead of `name · input_kind`, and gets a tag
  filter (default All).
- User guide: the Prompts section moves to the Administration chapter and explains tags; the Generate documents
  section mentions the tag filter.

## Tests

- Migration: backfill tags from `input_kind`; seed trigger tags a new workspace's prompts; tag CHECK refuses bad tags,
  more than 10 and duplicates; down → up round trip.
- API: create/update with tags (normalised, merged); invalid and too many tags refused with the messages above;
  `?tag=` filter; tags endpoint counts; `input_kind` no longer accepted (extra field refused) or returned; changing
  only tags does not add a version.
- UI: tags input (add, remove, invalid hint, suggestions), list badges and filter, redirects from the old addresses,
  Generate documents picker shows tags and filters.

## Not in scope

- The server discovery prompt and its `server-discovery` tag (spec 2 seeds them).
- Renaming the `documentation_prompts` table or the API path.
