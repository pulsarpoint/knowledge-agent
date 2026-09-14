#!/bin/sh
# Run golang-migrate with PostgreSQL connection settings from PG* environment variables.
# Use `adopt` once for a database managed by the legacy checksum runner.
set -eu
LC_ALL=C
export LC_ALL

db_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
migration_source="$db_dir/migrations"
database_url=${MIGRATION_DATABASE_URL:-postgres:///}

case "$database_url" in
    *x-migrations-table=golang_schema_migrations*) ;;
    *x-migrations-table=*)
        echo 'MIGRATION_DATABASE_URL must use golang_schema_migrations.' >&2
        exit 2
        ;;
    *\?*) database_url="${database_url}&x-migrations-table=golang_schema_migrations" ;;
    *) database_url="${database_url}?x-migrations-table=golang_schema_migrations" ;;
esac

run_migrate() {
    migrate -path "$migration_source" -database "$database_url" "$@"
}

adopt_legacy_database() {
    if ! command -v psql >/dev/null 2>&1; then
        echo 'Legacy adoption requires psql and PG* connection variables.' >&2
        exit 2
    fi

    legacy_exists=$(psql -X -Atqc "SELECT to_regclass('public.schema_migrations') IS NOT NULL")
    if [ "$legacy_exists" != t ]; then
        echo 'Legacy public.schema_migrations was not found; run migrations normally.' >&2
        exit 2
    fi

    golang_exists=$(psql -X -Atqc "SELECT to_regclass('public.golang_schema_migrations') IS NOT NULL")
    if [ "$golang_exists" = t ]; then
        echo 'golang_schema_migrations already exists; adoption is a one-time operation.' >&2
        exit 2
    fi

    legacy_count=$(psql -X -Atqc 'SELECT count(*) FROM public.schema_migrations')
    case "$legacy_count" in
        ''|*[!0-9]*) echo 'Could not read the legacy migration count.' >&2; exit 2 ;;
        0) echo 'The legacy ledger is empty; run migrations normally.' >&2; exit 2 ;;
    esac

    file_count=0
    baseline_version=
    for migration_file in "$migration_source"/[0-9]*.up.sql; do
        [ -f "$migration_file" ] || continue
        file_count=$((file_count + 1))
        [ "$file_count" -le "$legacy_count" ] || continue

        migration_name=$(basename "$migration_file" .up.sql)
        case "$migration_name" in
            *[!a-z0-9_]*) echo "Invalid migration name: $migration_name" >&2; exit 2 ;;
        esac
        padded_version=${migration_name%%_*}
        migration_number=$(printf '%s' "$padded_version" | sed 's/^0*//')
        [ -n "$migration_number" ] || migration_number=0
        if [ "$migration_number" -ne "$file_count" ]; then
            echo "Migration files are not contiguous at $migration_name." >&2
            exit 2
        fi
        if command -v sha256sum >/dev/null 2>&1; then
            expected_checksum=$(sha256sum "$migration_file" | cut -d ' ' -f 1)
        else
            expected_checksum=$(shasum -a 256 "$migration_file" | cut -d ' ' -f 1)
        fi
        recorded_checksum=$(psql -X -Atqc "SELECT checksum FROM public.schema_migrations WHERE version = '$migration_name'")
        if [ -z "$recorded_checksum" ]; then
            echo "Legacy migration sequence is not contiguous at $migration_name." >&2
            exit 2
        fi
        if [ "$recorded_checksum" != "$expected_checksum" ]; then
            echo "Legacy migration checksum mismatch at $migration_name." >&2
            exit 2
        fi

        baseline_version=$migration_number
    done

    if [ "$legacy_count" -gt "$file_count" ]; then
        echo 'The legacy ledger contains a migration not present in this checkout.' >&2
        exit 2
    fi

    run_migrate force "$baseline_version"
    echo "Verified $legacy_count legacy migrations and adopted version $baseline_version."
}

if [ "$#" -eq 0 ]; then
    set -- up
fi

if [ "$1" = adopt ]; then
    [ "$#" -eq 1 ] || { echo 'Usage: migrate.sh adopt' >&2; exit 2; }
    adopt_legacy_database
    exit 0
fi

run_migrate "$@"
