#!/bin/sh
# Uses disposable databases, containers, and a private Docker network only.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
test_container="ka-v3-schema-test-$$"
test_network="ka-v3-schema-test-$$"
test_workdir=$(mktemp -d)
test_log="$test_workdir/test.log"
first_race_log="$test_workdir/first-race.log"
second_race_log="$test_workdir/second-race.log"
migrate_image=knowledge-agent-v3-migrate:4.19.1

cleanup() {
    docker rm -f "$test_container" >/dev/null 2>&1 || true
    docker network rm "$test_network" >/dev/null 2>&1 || true
    rm -rf "$test_workdir"
}
trap cleanup EXIT HUP INT TERM

run_migrate() {
    database_name=$1
    shift
    docker run --rm --network "$test_network" \
        -e PGHOST="$test_container" \
        -e PGUSER=postgres \
        -e PGDATABASE="$database_name" \
        -e PGSSLMODE=disable \
        -v "$repo_dir/db:/workspace/db:ro" \
        "$migrate_image" "$@"
}

docker network create "$test_network" >/dev/null
docker run -d --rm --name "$test_container" \
    --network "$test_network" \
    -e POSTGRES_HOST_AUTH_METHOD=trust \
    --tmpfs /var/lib/postgresql/data \
    -v "$repo_dir/db:/workspace/db:ro" \
    knowledge-agent-v3-postgres:17.10-vector0.8.6 > /dev/null

ready=0
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if docker exec "$test_container" pg_isready -U postgres >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" != 1 ]; then docker logs "$test_container"; exit 1; fi

# The old collection pipeline is retired by migration 39; test its historical schema at 38.
run_migrate postgres goto 38 > "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
docker exec "$test_container" psql -X -U postgres -v ON_ERROR_STOP=1 \
    -f /workspace/db/tests/schema.sql > "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
sed -n '/^PASS:/p' "$test_log"
run_migrate postgres up > "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
run_migrate postgres up >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
rolled_back_version=$(docker exec "$test_container" psql -X -U postgres -Atc \
    'SELECT version FROM public.golang_schema_migrations WHERE NOT dirty')
[ "$rolled_back_version" = 71 ]
run_migrate postgres goto 38 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
run_migrate postgres up >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
# Retain the coordinator migration regression as well as the latest rollback.
run_migrate postgres goto 31 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
docker exec "$test_container" psql -X -U postgres -v ON_ERROR_STOP=1 -c \
    'UPDATE ka.agents SET enabled=false WHERE is_coordinator' > /dev/null
run_migrate postgres up >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
coordinator_enabled=$(docker exec "$test_container" psql -X -U postgres -Atc \
    'SELECT enabled FROM ka.agents WHERE is_coordinator')
[ "$coordinator_enabled" = t ]
printf '%s\n' 'PASS: upgrade re-enables an existing disabled coordinator.'
docker exec "$test_container" psql -X -U postgres -v ON_ERROR_STOP=1 \
    -f /workspace/db/tests/documentation.sql > "$test_log" 2>&1 || {
    cat "$test_log"; exit 1;
}
sed -n '/^PASS:/p' "$test_log"
printf '%s\n' 'PASS: fresh migration and second no-change run use a clean golang-migrate ledger.'
printf '%s\n' 'PASS: documentation migrations roll back to version 38 and reapply; coordinator protection reapplies from version 31.'

docker exec "$test_container" psql -X -U postgres -v ON_ERROR_STOP=1 -c "
    WITH project AS (
        INSERT INTO ka.projects(slug, name) VALUES ('concurrency', 'Concurrency') RETURNING id
    )
    INSERT INTO ka.repositories(project_id, slug, name, remote_url)
    SELECT id, 'repo', 'Repo', 'https://github.com/example/repo.git' FROM project;
" > /dev/null
race_sql="BEGIN;
    INSERT INTO ka.jobs(project_id, kind, repository_id)
    SELECT project_id, 'repository_sync', id FROM ka.repositories WHERE slug = 'repo';
    SELECT pg_sleep(1);
    COMMIT;"
docker exec "$test_container" psql -X -U postgres -v ON_ERROR_STOP=1 -c "$race_sql" > "$first_race_log" 2>&1 &
first_race_pid=$!
docker exec "$test_container" psql -X -U postgres -v ON_ERROR_STOP=1 -c "$race_sql" > "$second_race_log" 2>&1 &
second_race_pid=$!
first_race_status=0
second_race_status=0
wait "$first_race_pid" || first_race_status=$?
wait "$second_race_pid" || second_race_status=$?
if ! { [ "$first_race_status" = 0 ] && [ "$second_race_status" != 0 ]; } && \
   ! { [ "$first_race_status" != 0 ] && [ "$second_race_status" = 0 ]; }; then
    cat "$first_race_log" "$second_race_log"
    echo 'FAIL: expected exactly one concurrent sync job to succeed' >&2
    exit 1
fi
race_count=$(docker exec "$test_container" psql -X -U postgres -Atc 'SELECT count(*) FROM ka.jobs')
[ "$race_count" = 1 ]
printf '%s\n' 'PASS: concurrent requests create exactly one live job.'

# An explicit transaction keeps a failed migration's DDL out while golang-migrate
# retains its dirty version for an operator to inspect and repair.
cp -R "$repo_dir/db" "$test_workdir/broken-db"
printf '%s\n' \
    'BEGIN;' \
    'CREATE TABLE public.rollback_probe(id integer);' \
    'SELECT no_such_function();' \
    'COMMIT;' \
    > "$test_workdir/broken-db/migrations/9999_broken.up.sql"
if docker run --rm --network "$test_network" \
    -e PGHOST="$test_container" -e PGUSER=postgres -e PGDATABASE=postgres -e PGSSLMODE=disable \
    -v "$test_workdir/broken-db:/workspace/db:ro" "$migrate_image" up > "$test_log" 2>&1; then
    echo 'FAIL: broken migration unexpectedly succeeded' >&2
    exit 1
fi
dirty_ok=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('public.rollback_probe') IS NULL AND version = 9999 AND dirty FROM public.golang_schema_migrations")
[ "$dirty_ok" = t ]
if docker run --rm --network "$test_network" \
    -e PGHOST="$test_container" -e PGUSER=postgres -e PGDATABASE=postgres -e PGSSLMODE=disable \
    -v "$test_workdir/broken-db:/workspace/db:ro" "$migrate_image" up > "$test_log" 2>&1; then
    echo 'FAIL: a dirty migration version unexpectedly accepted another up run' >&2
    exit 1
fi
printf '%s\n' 'PASS: failed transactional migration rolls back DDL and leaves a dirty version.'

# Build a populated version-24 database and its original checksum ledger.
docker exec "$test_container" createdb -U postgres adoption_test
docker exec "$test_container" psql -X -U postgres -d adoption_test -v ON_ERROR_STOP=1 -c '
    CREATE TABLE public.schema_migrations (
        version text PRIMARY KEY,
        checksum text NOT NULL,
        applied_at timestamptz NOT NULL DEFAULT now()
    );
' > /dev/null
for migration_file in "$repo_dir"/db/migrations/[0-9]*.up.sql; do
    migration_name=$(basename "$migration_file" .up.sql)
    padded_version=${migration_name%%_*}
    [ "$padded_version" \> 0024 ] && continue
    docker exec -i "$test_container" psql -X -1 -U postgres -d adoption_test -v ON_ERROR_STOP=1 \
        < "$migration_file" > /dev/null
    if command -v sha256sum >/dev/null 2>&1; then
        migration_checksum=$(sha256sum "$migration_file" | cut -d ' ' -f 1)
    else
        migration_checksum=$(shasum -a 256 "$migration_file" | cut -d ' ' -f 1)
    fi
    docker exec "$test_container" psql -X -U postgres -d adoption_test -v ON_ERROR_STOP=1 -c \
        "INSERT INTO public.schema_migrations(version, checksum) VALUES ('$migration_name', '$migration_checksum')" > /dev/null
done
docker exec "$test_container" psql -X -U postgres -d adoption_test -v ON_ERROR_STOP=1 -c \
    "INSERT INTO ka.projects(slug, name) VALUES ('adoption-sentinel', 'Adoption sentinel')" > /dev/null

# A gap and a checksum mismatch must both block the one-time baseline.
checksum_0012=$(docker exec "$test_container" psql -X -U postgres -d adoption_test -Atc \
    "SELECT checksum FROM public.schema_migrations WHERE version = '0012_llm_settings'")
docker exec "$test_container" psql -X -U postgres -d adoption_test -c \
    "DELETE FROM public.schema_migrations WHERE version = '0012_llm_settings'" > /dev/null
if run_migrate adoption_test adopt > "$test_log" 2>&1; then
    echo 'FAIL: adoption unexpectedly accepted a gap in the legacy sequence' >&2
    exit 1
fi
docker exec "$test_container" psql -X -U postgres -d adoption_test -c \
    "INSERT INTO public.schema_migrations(version, checksum) VALUES ('0012_llm_settings', '$checksum_0012')" > /dev/null
docker exec "$test_container" psql -X -U postgres -d adoption_test -c \
    "UPDATE public.schema_migrations SET checksum = repeat('0', 64) WHERE version = '0012_llm_settings'" > /dev/null
if run_migrate adoption_test adopt > "$test_log" 2>&1; then
    echo 'FAIL: adoption unexpectedly accepted a legacy checksum mismatch' >&2
    exit 1
fi
docker exec "$test_container" psql -X -U postgres -d adoption_test -c \
    "UPDATE public.schema_migrations SET checksum = '$checksum_0012' WHERE version = '0012_llm_settings'" > /dev/null

run_migrate adoption_test adopt > "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
adopted_ok=$(docker exec "$test_container" psql -X -U postgres -d adoption_test -Atc \
    "SELECT version = 24 AND NOT dirty AND
        (SELECT count(*) = 24 FROM public.schema_migrations) AND
        (SELECT count(*) = 1 FROM ka.projects WHERE slug = 'adoption-sentinel')
     FROM public.golang_schema_migrations")
[ "$adopted_ok" = t ]
run_migrate adoption_test up >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
run_migrate adoption_test up >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
retained_ok=$(docker exec "$test_container" psql -X -U postgres -d adoption_test -Atc \
    "SELECT NOT dirty AND version >= 24 AND
        (SELECT count(*) = 24 FROM public.schema_migrations) AND
        (SELECT count(*) = 1 FROM ka.projects WHERE slug = 'adoption-sentinel')
     FROM public.golang_schema_migrations")
[ "$retained_ok" = t ]
printf '%s\n' 'PASS: verified adoption preserves legacy evidence and populated records, then applies new migrations once.'
