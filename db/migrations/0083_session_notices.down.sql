BEGIN;
SET search_path = ka, public;

DROP TRIGGER notify_session_input ON agent_session_inputs;
DROP TRIGGER notify_session_removal ON agent_sessions_cc;
DROP TRIGGER notify_session_change ON agent_sessions_cc;
DROP TRIGGER notify_session_events ON agent_run_events;
DROP FUNCTION notify_session_input();
DROP FUNCTION notify_session_change();
DROP FUNCTION notify_session_events();

COMMIT;
