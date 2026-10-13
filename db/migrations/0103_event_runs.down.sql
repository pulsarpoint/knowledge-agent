BEGIN;
SET search_path = ka, public;

-- Agent tasks 0103 failed stay failed: nothing to undo.

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agent_sessions WHERE event_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Event runs exist; delete them before rollback';
    END IF;
END $$;
ALTER TABLE notifications DROP COLUMN session_id;
DROP INDEX agent_sessions_event_handler;
ALTER TABLE agent_sessions
    DROP COLUMN handler_id,
    DROP COLUMN event_id;

COMMIT;
