BEGIN;
SET search_path = ka, public;

-- A Send-to-agent handler starts an ordinary agent run (purpose event, the saved agent, the rendered prompt as its
-- first message) instead of queueing an agent task. The run records the event and handler that started it: one run per
-- event and handler, so processing an event again starts nothing new. A notification links to the run it reports on.
ALTER TABLE agent_sessions
    ADD COLUMN event_id bigint REFERENCES events(id) ON DELETE SET NULL,
    ADD COLUMN handler_id uuid REFERENCES event_handlers(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX agent_sessions_event_handler ON agent_sessions(event_id, handler_id)
    WHERE event_id IS NOT NULL AND handler_id IS NOT NULL;
COMMENT ON COLUMN agent_sessions.event_id IS 'The event whose Send-to-agent handler started this run (purpose event).';
COMMENT ON COLUMN agent_sessions.handler_id IS 'The Send-to-agent handler that started this run (purpose event); one run per event and handler.';

ALTER TABLE notifications ADD COLUMN session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL;
COMMENT ON COLUMN notifications.session_id IS 'The agent run (started by an event handler) this notification reports on.';

-- Agent tasks are no longer run. Tasks still queued or running are failed, each with a failed notification, so nothing
-- waits for an answer that never comes. ka.agent_tasks stays as read-only history.
WITH written AS (
    INSERT INTO notifications(project_id, kind, status, title, body, event_id, handler_id, agent_id, agent_task_id)
    SELECT project_id, 'agent.response', 'failed', title,
        'Not run: event handlers start agent runs now. Send the event again.', event_id, handler_id, agent_id, id
    FROM agent_tasks WHERE status IN ('queued', 'running')
    RETURNING id, agent_task_id
)
UPDATE agent_tasks t SET status = 'failed', error = 'Not run: event handlers start agent runs now. Send the event again.',
    worker_id = NULL, lease_expires_at = NULL, finished_at = now(), notification_id = written.id
FROM written WHERE t.id = written.agent_task_id;

COMMIT;
