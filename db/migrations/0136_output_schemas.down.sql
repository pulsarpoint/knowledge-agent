BEGIN;
SET search_path = ka, public;

-- Back to 0135: no output schemas.
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_output_schema_pinned,
    DROP COLUMN output,
    DROP COLUMN output_schema_id,
    DROP COLUMN output_schema_version;
ALTER TABLE event_handlers
    DROP CONSTRAINT event_handlers_output_schema_pinned,
    DROP CONSTRAINT event_handlers_output_schema_version,
    DROP COLUMN output_schema_id,
    DROP COLUMN output_schema_version;
DROP TABLE output_schema_versions;
DROP TABLE output_schemas;

COMMIT;
