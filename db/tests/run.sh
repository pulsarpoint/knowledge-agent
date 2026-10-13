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
discovery_v1=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT prosrc LIKE '%Explore this server and propose%' FROM pg_proc
        WHERE oid = 'ka.seed_server_discovery_prompt(uuid)'::regprocedure")
[ "$discovery_v1" = t ] || { echo 'FAIL: 0102 down left the new Server discovery seed' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
write_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regprocedure('ka.seed_write_document_prompt(uuid)') IS NULL")
[ "$write_removed" = t ] || { echo 'FAIL: 0101 down left the Write a document seed' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
revise_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regprocedure('ka.seed_document_revision_prompt(uuid)') IS NULL")
[ "$revise_removed" = t ] || { echo 'FAIL: 0100 down left the Revise a document seed' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
agent_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ka'
        AND table_name = 'agent_sessions' AND column_name = 'target_document_id')")
[ "$agent_removed" = t ] || { echo 'FAIL: 0099 down left Ask agent runs' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
drafts_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.document_drafts') IS NULL AND to_regclass('ka.document_sources') IS NULL
        AND to_regclass('ka.document_links') IS NULL")
[ "$drafts_removed" = t ] || { echo 'FAIL: 0098 down left document drafts' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
discovery_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.discovery_proposals') IS NULL AND NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'ka' AND table_name = 'agent_sessions' AND column_name = 'server_id')")
[ "$discovery_removed" = t ] || { echo 'FAIL: 0097 down left discovery runs' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
tags_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ka'
        AND table_name = 'documentation_prompts' AND column_name = 'tags')")
[ "$tags_removed" = t ] || { echo 'FAIL: 0096 down left prompt tags' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
links_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.inventory_links') IS NULL AND to_regclass('ka.kg_nodes') IS NOT NULL
        AND to_regclass('ka.service_dependencies') IS NOT NULL")
[ "$links_removed" = t ] || { echo 'FAIL: 0095 down did not restore the graph and link tables' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
tree_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT pg_get_indexdef('ka.kg_nodes_by_server'::regclass) NOT LIKE '%environment_id%'")
[ "$tree_removed" = t ] || { echo 'FAIL: 0094 down left server nodes per environment' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
sync_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ka'
        AND table_name = 'kg_nodes' AND column_name = 'server_id')")
[ "$sync_removed" = t ] || { echo 'FAIL: 0093 down left the inventory links' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
graph_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.kg_nodes') IS NULL AND to_regclass('ka.kg_edges') IS NULL
        AND to_regclass('ka.kg_node_documents') IS NULL")
[ "$graph_removed" = t ] || { echo 'FAIL: 0092 down left system graph tables' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
manual_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT pg_get_constraintdef((SELECT oid FROM pg_constraint WHERE conname = 'events_source_check')) NOT LIKE '%manual%'")
[ "$manual_removed" = t ] || { echo 'FAIL: 0091 down left manual in the event source check' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
tasks_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.agent_tasks') IS NULL AND to_regclass('ka.notifications') IS NULL")
[ "$tasks_removed" = t ] || { echo 'FAIL: 0090 down left agent tasks or notifications' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
handlers_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.event_handlers') IS NULL AND to_regclass('ka.event_handler_runs') IS NULL
        AND NOT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'ka' AND table_name = 'events' AND column_name = 'status')")
[ "$handlers_removed" = t ] || { echo 'FAIL: 0089 down left handler tables or event status' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
events_removed=$(docker exec "$test_container" psql -X -U postgres -Atc "SELECT to_regclass('ka.events') IS NULL")
[ "$events_removed" = t ] || { echo 'FAIL: 0088 down left ka.events' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
params_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'ka' AND table_name = 'schedules' AND column_name = 'params')")
[ "$params_removed" = t ] || { echo 'FAIL: 0087 down left schedules.params' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
schedules_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.schedules') IS NULL AND to_regclass('ka.schedule_runs') IS NULL")
[ "$schedules_removed" = t ] || { echo 'FAIL: 0086 down left the schedule tables' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
experts_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'ka' AND table_name = 'agents' AND column_name = 'expert')")
experts_index_gone=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.agent_run_events_expert_calls') IS NULL")
[ "$experts_removed" = t ] && [ "$experts_index_gone" = t ] || { echo 'FAIL: 0085 down left agents.expert or its index' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
rolled_back_version=$(docker exec "$test_container" psql -X -U postgres -Atc \
    'SELECT version FROM public.golang_schema_migrations WHERE NOT dirty')
[ "$rolled_back_version" = 83 ]
renamed_back=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.agent_sessions_cc') IS NOT NULL AND to_regclass('ka.agent_sessions') IS NULL")
[ "$renamed_back" = t ] || { echo 'FAIL: 0084 down did not restore agent_sessions_cc' >&2; exit 1; }
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
notices_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname IN
            ('notify_session_events', 'notify_session_change', 'notify_session_removal', 'notify_session_input'))
        AND to_regprocedure('ka.notify_session_events()') IS NULL
        AND to_regprocedure('ka.notify_session_change()') IS NULL
        AND to_regprocedure('ka.notify_session_input()') IS NULL")
[ "$notices_removed" = t ] || { echo 'FAIL: 0083 down left session notice triggers' >&2; exit 1; }
run_migrate postgres goto 38 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
run_migrate postgres up >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
# The board and the coordinator: roll back to 29, before 0030 seeded the coordinator settings that 0031 turns into the
# coordinator agent (0077 down restores the tables empty), and upgrade again; 0031 creates the agent with its prompt
# and 0077 removes it, the board and every coordinator rule.
run_migrate postgres goto 29 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
run_migrate postgres up >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
coordinator_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.agent_board_tasks') IS NULL AND to_regclass('ka.agent_board_members') IS NULL
        AND to_regclass('ka.coordinator_settings') IS NULL
        AND NOT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'ka' AND table_name = 'agents' AND column_name = 'is_coordinator')
        AND NOT EXISTS (SELECT 1 FROM ka.agents WHERE id = 'coordinator')
        AND to_regclass('ka.agent_prompt_versions') IS NULL")
[ "$coordinator_removed" = t ]
printf '%s\n' 'PASS: the board and coordinator schema rolls back to version 29 and is removed again on upgrade.'
# 0079 fills agents.system_prompt and then drops its temporary default: every new agent names its prompt.
prompt_default=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT column_default IS NULL AND is_nullable = 'NO' FROM information_schema.columns
        WHERE table_schema = 'ka' AND table_name = 'agents' AND column_name = 'system_prompt'")
[ "$prompt_default" = t ] || { echo 'FAIL: agents.system_prompt still has a default' >&2; exit 1; }
printf '%s\n' 'PASS: agents.system_prompt is required and has no default.'
# 0081 runs Test chats as sessions and 0084 renames agent_sessions_cc to agent_sessions: the old Test chat tables are
# gone and agent_sessions has a purpose.
test_sessions=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.agent_sessions_cc') IS NULL AND to_regclass('ka.operations_runs') IS NULL
        AND to_regclass('ka.operations_commands') IS NULL
        AND EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'ka' AND table_name = 'agent_sessions' AND column_name = 'purpose')")
[ "$test_sessions" = t ] || { echo 'FAIL: the old Test chat tables are still there' >&2; exit 1; }
printf '%s\n' 'PASS: Test chats are sessions; the old Test chat tables are gone.'
# 0083 announces session changes on ka_sessions: run events, session rows (not lease-only updates) and queued messages.
session_notices=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT count(*) = 4 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'ka' AND NOT t.tgisinternal AND (c.relname, t.tgname) IN
            (('agent_run_events', 'notify_session_events'), ('agent_sessions', 'notify_session_change'),
             ('agent_sessions', 'notify_session_removal'), ('agent_session_inputs', 'notify_session_input'))")
[ "$session_notices" = t ] || { echo 'FAIL: the session notice triggers are missing' >&2; exit 1; }
printf '%s\n' 'PASS: session changes are announced on ka_sessions.'
# 0085 offers agents as experts; a caller's expert:<id> rows are never given to subagents.
experts=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'ka' AND table_name = 'agents' AND column_name = 'expert')
        AND pg_get_constraintdef((SELECT oid FROM pg_constraint WHERE conname = 'subagents_do_not_spawn')) LIKE '%expert:%'")
experts_index=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.agent_run_events_expert_calls') IS NOT NULL")
[ "$experts" = t ] && [ "$experts_index" = t ] || { echo 'FAIL: 0085 expert column, check or index missing' >&2; exit 1; }
printf '%s\n' 'PASS: agents can be experts; expert rows are never given to subagents.'
# 0086 adds schedules and their run history.
schedules=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.schedules') IS NOT NULL AND to_regclass('ka.schedule_runs') IS NOT NULL
        AND to_regclass('ka.schedules_due') IS NOT NULL")
[ "$schedules" = t ] || { echo 'FAIL: 0086 schedule tables missing' >&2; exit 1; }
printf '%s\n' 'PASS: schedules and their run history exist.'
params=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'ka' AND table_name = 'schedules' AND column_name = 'params')")
[ "$params" = t ] || { echo 'FAIL: 0087 schedules.params missing' >&2; exit 1; }
printf '%s\n' 'PASS: schedules have target params.'
events=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.events') IS NOT NULL AND to_regclass('ka.events_jira_issues') IS NOT NULL")
[ "$events" = t ] || { echo 'FAIL: 0088 events table missing' >&2; exit 1; }
printf '%s\n' 'PASS: events are recorded in ka.events.'
handlers=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.event_handlers') IS NOT NULL AND to_regclass('ka.event_handler_runs') IS NOT NULL
        AND to_regclass('ka.events_pending') IS NOT NULL")
[ "$handlers" = t ] || { echo 'FAIL: 0089 event handlers missing' >&2; exit 1; }
printf '%s\n' 'PASS: events have a status and a chain; handlers and their runs exist.'
tasks=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.agent_tasks') IS NOT NULL AND to_regclass('ka.notifications') IS NOT NULL
        AND to_regclass('ka.agent_tasks_due') IS NOT NULL")
[ "$tasks" = t ] || { echo 'FAIL: 0090 agent tasks or notifications missing' >&2; exit 1; }
printf '%s\n' 'PASS: agent tasks and notifications exist.'
manual=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT pg_get_constraintdef((SELECT oid FROM pg_constraint WHERE conname = 'events_source_check')) LIKE '%manual%'")
[ "$manual" = t ] || { echo 'FAIL: 0091 events.source does not allow manual' >&2; exit 1; }
printf '%s\n' 'PASS: events can be made by hand (source manual).'
links=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.inventory_links') IS NOT NULL AND to_regclass('ka.inventory_documents') IS NOT NULL
        AND to_regclass('ka.kg_nodes') IS NULL AND to_regclass('ka.service_dependencies') IS NULL")
[ "$links" = t ] || { echo 'FAIL: 0095 inventory links missing or the stored graph still there' >&2; exit 1; }
printf '%s\n' 'PASS: inventory links replace the stored system graph.'
tags=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ka'
        AND table_name = 'documentation_prompts' AND column_name = 'tags')")
[ "$tags" = t ] || { echo 'FAIL: 0096 prompt tags missing' >&2; exit 1; }
printf '%s\n' 'PASS: prompts carry tags.'
discovery=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.discovery_proposals') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'ka' AND table_name = 'agent_sessions' AND column_name = 'server_id')")
[ "$discovery" = t ] || { echo 'FAIL: 0097 discovery runs missing' >&2; exit 1; }
printf '%s\n' 'PASS: discovery runs and proposals exist.'
drafts=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.document_drafts') IS NOT NULL AND to_regclass('ka.document_sources') IS NOT NULL
        AND to_regclass('ka.document_links') IS NOT NULL")
[ "$drafts" = t ] || { echo 'FAIL: 0098 document drafts missing' >&2; exit 1; }
printf '%s\n' 'PASS: document drafts, sources and links exist.'
agent=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ka'
        AND table_name = 'agent_sessions' AND column_name = 'target_document_id')")
[ "$agent" = t ] || { echo 'FAIL: 0099 Ask agent runs missing' >&2; exit 1; }
printf '%s\n' 'PASS: Ask agent runs name their document.'
revise=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regprocedure('ka.seed_document_revision_prompt(uuid)') IS NOT NULL")
[ "$revise" = t ] || { echo 'FAIL: 0100 Revise a document seed missing' >&2; exit 1; }
printf '%s\n' 'PASS: the Revise a document prompt is seeded.'
write=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regprocedure('ka.seed_write_document_prompt(uuid)') IS NOT NULL")
[ "$write" = t ] || { echo 'FAIL: 0101 Write a document seed missing' >&2; exit 1; }
printf '%s\n' 'PASS: the Write a document prompt is seeded.'
discovery_v2=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT prosrc LIKE '%Explore the server named in the context%' FROM pg_proc
        WHERE oid = 'ka.seed_server_discovery_prompt(uuid)'::regprocedure")
[ "$discovery_v2" = t ] || { echo 'FAIL: 0102 Server discovery seed not rewritten' >&2; exit 1; }
printf '%s\n' 'PASS: the Server discovery prompt names the server through the context.'
docker exec "$test_container" psql -X -U postgres -v ON_ERROR_STOP=1 \
    -f /workspace/db/tests/documentation.sql > "$test_log" 2>&1 || {
    cat "$test_log"; exit 1;
}
sed -n '/^PASS:/p' "$test_log"
printf '%s\n' 'PASS: fresh migration and second no-change run use a clean golang-migrate ledger.'
printf '%s\n' 'PASS: documentation migrations roll back to version 38 and reapply.'

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
