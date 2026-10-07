COMPOSE = docker compose --env-file .env -f deploy/compose.yaml
export ADMIN_USERNAME ADMIN_EMAIL

.PHONY: setup up down status logs build build-ui build-api build-docker smoke test test-api test-ui test-setup db-up db-down db-migrate db-adopt db-test compose-validate index
setup:
	python3 scripts/setup.py

up: setup
	$(COMPOSE) up -d --build --wait --wait-timeout 240

down:
	$(COMPOSE) down

status:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --tail=100 -f

.PHONY: jira-mock-up jira-mock-down
jira-mock-up:
	$(COMPOSE) --profile jira-mock up -d --build --wait jira-mock

jira-mock-down:
	$(COMPOSE) --profile jira-mock stop jira-mock

build: build-api build-ui

build-api:
	cd services/rag && uv sync --frozen
	cd services/admin-api && uv sync --frozen

build-ui:
	cd services/admin-ui && pnpm install --frozen-lockfile && pnpm build

build-docker: setup
	$(COMPOSE) build

smoke:
	python3 scripts/smoke.py

test: test-setup test-api test-ui db-test

test-setup:
	python3 -m unittest discover -s tests -v

test-api: db-test-image
	cd services/rag && uv run --frozen pytest -q
	cd services/admin-api && uv run --frozen pytest -q

test-ui:
	cd services/admin-ui && pnpm test

db-up: setup
	$(COMPOSE) up -d --wait postgres

db-down:
	$(COMPOSE) stop postgres

db-migrate:
	$(COMPOSE) run --rm migrate

db-adopt:
	$(COMPOSE) run --rm migrate adopt

.PHONY: db-test-image db-migrate-image
db-test-image:
	docker build -t knowledge-agent-v3-postgres:17.10-vector0.8.6 -f deploy/Dockerfile.postgres .

db-migrate-image:
	docker build -t knowledge-agent-v3-migrate:4.19.1 -f deploy/Dockerfile.migrate .

db-test: db-test-image db-migrate-image
	sh db/tests/run.sh

compose-validate: setup
	$(COMPOSE) config --quiet

index:
	@test -n "$(TARGET)" || { echo 'TARGET=<docs-folder|config.toml> is required'; exit 2; }
	cd services/rag && uv run --frozen rag index "$(abspath $(TARGET))"
