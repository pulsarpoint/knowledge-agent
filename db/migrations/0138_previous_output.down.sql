BEGIN;
SET search_path = ka, public;

-- Back to 0137: no schedule on a run, no previous output; a run's handler is a foreign key again (a deleted handler's
-- runs lose it).
ALTER TABLE event_handlers DROP COLUMN include_previous_output;
DROP INDEX agent_sessions_previous_output;
ALTER TABLE agent_sessions DROP COLUMN schedule_id;
UPDATE agent_sessions s SET handler_id = NULL
WHERE handler_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM event_handlers h WHERE h.id = s.handler_id);
ALTER TABLE agent_sessions
    ADD CONSTRAINT agent_sessions_handler_id_fkey FOREIGN KEY (handler_id) REFERENCES event_handlers(id) ON DELETE SET NULL;
COMMENT ON COLUMN agent_sessions.handler_id IS 'The Send-to-agent handler that started this run (purpose event); one run per event and handler.';

COMMIT;
