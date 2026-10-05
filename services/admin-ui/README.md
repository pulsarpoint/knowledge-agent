# Administration UI

Standalone React/shadcn application. It has its own package, build, routes and
navigation, including RAG search and isolated agent tests. It calls only the
standalone administration API.

```sh
pnpm install --frozen-lockfile
pnpm dev     # port 5174; proxies /api to the native admin API on port 8766
pnpm build
pnpm test
```

Use `make up` from the repository root to run through Keycloak on port 8183.
`make admin-user` provisions `local-admin`; its password is saved privately at
`.local/users/local-admin.json` in the repository root.

The root route opens `/rag/collections`. RAG includes Collections, Jobs, Settings,
and Search. `/github-projects`, `/services`, and `/servers` manage agent assets.
Jira settings and tickets live under `/jira`; `/skills`, `/llm-settings`, and
`/agents` configure and test agents. `/activity` supports user/session audit
inspection. The gateway and API require `docs-admins`; reader sessions do not
authenticate this application.
