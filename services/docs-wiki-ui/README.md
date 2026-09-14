# docs-wiki-ui

The docs-wiki frontend: a static React SPA (React Router framework
mode, SPA build) over the docs-wiki `/api` — reading with a collapsible
dashboard-shell sidebar (icon-collapse desktop, mobile Sheet) and freshness
badges, as-you-type search, the documentation gaps page, and streaming chat
with shareable conversation URLs. Full mobile support.

Administration is a separate application in `services/admin-ui`, with its own API
in `services/admin-api`. This reader has no admin navigation, routes or API client.

## Develop

    pnpm install
    pnpm dev        # http://localhost:5173, proxies /api -> 127.0.0.1:8765

Run the API alongside: `uv run docs-wiki serve <config>` from
`services/docs-wiki`. No CORS setup is needed — the dev server proxies.

## Build and test

    pnpm test           # vitest (jsdom, mocked fetch/SSE — no server needed)
    pnpm build      # typecheck + SPA build into build/client/

Serving the built app (single-process via `docs-wiki serve` or the
nginx compose bundle) is documented in the main service README.

## Security invariants

- Markdown (docs AND model answers) renders with raw HTML disabled;
  search snippets honor only the API's `<mark>` tags. Do not add
  rehype-raw or dangerouslySetInnerHTML (the constant theme snippet in
  root.tsx and the vendored Mermaid.tsx for SVG injection are documented
  exceptions).
- The only network calls are same-origin `/api/...`; all assets are
  bundled at build time (air-gap requirement).
- Conversation keys are capability grants: they appear in URLs by
  design; the recent-conversations list stores keys and first
  questions only, in this browser's localStorage.

Vendored code and its provenance: see VENDORED.md.
