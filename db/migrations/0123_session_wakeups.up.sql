BEGIN;
SET search_path = ka, public;

-- 0123: wake-ups: an agent queues a message to its own session for later (schedule_wakeup). A wake-up is an input
-- with kind 'wakeup' and a not_before time; the session worker delivers it as the next turn once it is due.
ALTER TABLE agent_session_inputs
    ADD COLUMN not_before timestamptz,
    ADD COLUMN kind text NOT NULL DEFAULT 'message',
    ADD CONSTRAINT agent_session_inputs_kind CHECK (kind IN ('message', 'wakeup')),
    ADD CONSTRAINT agent_session_inputs_wakeup_time CHECK (kind <> 'wakeup' OR not_before IS NOT NULL);
COMMENT ON COLUMN agent_session_inputs.not_before IS
    'Not delivered before this time (null: as soon as possible). Set for wake-ups.';
COMMENT ON COLUMN agent_session_inputs.kind IS
    'message: a person''s message; wakeup: a message the agent scheduled for itself (text is its message).';
CREATE INDEX agent_session_inputs_wakeups ON agent_session_inputs(not_before)
    WHERE status = 'queued' AND kind = 'wakeup';

COMMIT;
