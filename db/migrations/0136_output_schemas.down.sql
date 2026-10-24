BEGIN;
SET search_path = ka, public;

-- Back to 0135: no output schemas.
DELETE FROM event_handler_runs WHERE status = 'skipped';
ALTER TABLE event_handler_runs
    DROP CONSTRAINT event_handler_runs_status_check,
    ADD CONSTRAINT event_handler_runs_status_check CHECK (status IN ('succeeded', 'failed'));
ALTER TABLE llm_models DROP COLUMN capabilities;
DELETE FROM events WHERE source = 'run';
ALTER TABLE events
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira', 'handler', 'manual', 'memory', 'schedule'));
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_output_schema_pinned,
    DROP COLUMN output,
    DROP COLUMN output_error,
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
