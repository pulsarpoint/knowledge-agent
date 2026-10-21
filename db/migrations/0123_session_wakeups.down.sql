BEGIN;
SET search_path = ka, public;

-- Wake-ups go (pending and delivered); person messages stay.
DELETE FROM agent_session_inputs WHERE kind = 'wakeup';
DROP INDEX agent_session_inputs_wakeups;
ALTER TABLE agent_session_inputs
    DROP CONSTRAINT agent_session_inputs_wakeup_time,
    DROP CONSTRAINT agent_session_inputs_kind,
    DROP COLUMN kind,
    DROP COLUMN not_before;

COMMIT;
