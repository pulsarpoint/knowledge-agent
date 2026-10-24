BEGIN;
SET search_path = ka, public;

-- 0138: a run records the handler and the schedule that started it, so a handler can include the previous output: the
-- last valid output of the same handler for the same schedule goes into each new run's task. Neither column has a
-- foreign key: a run outlives a deleted schedule or handler (the run page says "(deleted)").
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_handler_id_fkey,
    ADD COLUMN schedule_id uuid;
COMMENT ON COLUMN agent_sessions.handler_id IS
    'The Send-to-agent handler that started this run (purpose event); one run per event and handler. No foreign key: a run outlives its handler.';
COMMENT ON COLUMN agent_sessions.schedule_id IS
    'The schedule whose schedule.fired event is the root of the chain that started this run. No foreign key: a run outlives its schedule.';
CREATE INDEX agent_sessions_previous_output ON agent_sessions(handler_id, schedule_id, created_at DESC)
    WHERE output IS NOT NULL;

ALTER TABLE event_handlers ADD COLUMN include_previous_output boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN event_handlers.include_previous_output IS
    'Send to agent: add the last valid output of this handler (for the same schedule) to each run''s task.';

-- Existing runs: the handler from their handler run (also when the handler was deleted), the schedule from their event
-- chain's root (the event, or the event the run pinned when the event is gone).
UPDATE agent_sessions s SET handler_id = r.handler_id
FROM event_handler_runs r
WHERE r.agent_session_id = s.id AND s.handler_id IS NULL;
UPDATE agent_sessions s SET schedule_id = root.source_id
FROM events root
WHERE root.source = 'schedule' AND root.id = coalesce(
    (SELECT e.root_id FROM events e WHERE e.id = s.event_id), (s.agent_config->'event'->>'root_id')::bigint);

COMMIT;
