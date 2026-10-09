BEGIN;
SET search_path = ka, public;

DROP TABLE event_handler_runs;
DROP TABLE event_handlers;

DELETE FROM agent_sessions WHERE purpose = 'event';
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test'));
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session.';

DELETE FROM events WHERE source = 'handler';
DROP TRIGGER events_root ON events;
DROP FUNCTION events_root();
ALTER TABLE events
    DROP CONSTRAINT events_source_made,
    DROP CONSTRAINT events_handler_made,
    DROP CONSTRAINT events_processed_at,
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira')),
    DROP COLUMN processed_at,
    DROP COLUMN status,
    DROP COLUMN handler_id,
    DROP COLUMN depth,
    DROP COLUMN root_id,
    DROP COLUMN parent_id;

COMMIT;
