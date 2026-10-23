# Administration UI

Standalone React/shadcn application. It has its own package, build, routes and
navigation, including documents and isolated agent tests. It calls only the
standalone administration API.

```sh
pnpm install --frozen-lockfile
pnpm dev     # port 5174; proxies /api to the native admin API on port 8766
pnpm build
pnpm test
pnpm format  # Prettier; `pnpm format:check` only reports
```

Use `make up` from the repository root to run through Keycloak on port 8183.
Sign in as `v3-admin` with the password `APP_ADMIN_PASSWORD` from `.env`.

The root route opens `/documents`. `/code-projects`, `/services`, and `/servers` manage agent assets.
Jira settings and tickets live under `/jira`; `/skills`, `/llm-settings`, and
`/agents` configure and test agents. `/activity/runs` lists agent run records.
The gateway and API require `docs-admins`.
