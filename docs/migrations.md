# PostgreSQL migrations

The deployment uses the official `golang-migrate` CLI v4.19.1. Migration files
use `<version>_<name>.up.sql` and, when rollback is supported,
`<version>_<name>.down.sql`. The current version and dirty flag are stored in
`public.golang_schema_migrations`. The former checksum ledger,
`public.schema_migrations`, is retained on upgraded databases as adoption
evidence.

Fresh databases need no baseline step. `make db-migrate` applies all pending up
migrations, and the Compose application services wait for that command to
succeed. `make db-test` exercises a fresh install, a no-change rerun, legacy
adoption, record retention, and failed/dirty migration behavior in disposable
PostgreSQL databases.

## One-time adoption of an existing database

Do this before starting a checkout that uses golang-migrate. Do not let the
normal migration service run against the existing database first, because it
would try to replay migration 1.

1. Back up PostgreSQL and retain the current `.env`, especially the PostgreSQL
   password and credential encryption key.
2. Keep application services stopped while adopting, and verify that PostgreSQL
   contains `public.schema_migrations` from the legacy runner.
3. Build only the migration image with `make db-migrate-image`.
4. Run `make db-adopt` once. It verifies that every legacy version is a
   contiguous prefix of this checkout and that every stored SHA-256 checksum
   matches the original up SQL. It then records that last verified version in
   `golang_schema_migrations` without replaying schema SQL.
5. Run `make db-migrate` to apply migrations added after the verified baseline.
   A second `make db-migrate` should report no change.

`db-adopt` refuses an empty legacy ledger, a gap, an unknown migration, a
checksum mismatch, or any existing golang-migrate ledger. It never deletes or
updates `public.schema_migrations`. Investigate a refusal against a restored
copy rather than forcing the live database forward.

## Failure and rollback

New migrations that can be reversed include a real down file. Historical
migrations have no invented down files, so a request to migrate down through
them fails instead of reporting a rollback that did nothing. Put transaction
boundaries in migrations that need atomic multi-statement behavior.

When an up migration fails, golang-migrate records its version as dirty. Inspect
the migration error and database state before changing the ledger. After fixing
or manually reverting the failed migration, an operator can run the migration
container with `force <last-good-version>` and then `up`. Never use `force` to
bypass the legacy adoption checks.

The migration file format and CLI behavior follow the
[official migration format](https://github.com/golang-migrate/migrate/blob/master/MIGRATIONS.md),
[CLI documentation](https://github.com/golang-migrate/migrate/blob/master/cmd/migrate/README.md),
and [PostgreSQL driver options](https://github.com/golang-migrate/migrate/blob/master/database/postgres/README.md).
