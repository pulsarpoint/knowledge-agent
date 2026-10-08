BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Live session pages: a change to a session is announced on the channel ka_sessions (delivered on commit) and one
-- listener per admin-api process reads the new rows. Payload {"s": "<session id>"}, or for run events
-- {"s": "<session id>", "e": <newest inserted event id>}.
CREATE FUNCTION notify_session_events() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    found record;
BEGIN
    FOR found IN
        SELECT split_part(run_key, ':', 2) AS session_id, max(id) AS newest FROM new_rows
        WHERE run_key ~ '^cc:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9]+$'
        GROUP BY 1
    LOOP
        PERFORM pg_notify('ka_sessions', json_build_object('s', found.session_id, 'e', found.newest)::text);
    END LOOP;
    RETURN NULL;
END $$;
CREATE TRIGGER notify_session_events AFTER INSERT ON agent_run_events
    REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION notify_session_events();

-- Any change to a session row (status, tokens, draft, turns, leases, stop, close) and its removal.
CREATE FUNCTION notify_session_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM pg_notify('ka_sessions', json_build_object('s', OLD.id)::text);
    ELSE
        PERFORM pg_notify('ka_sessions', json_build_object('s', NEW.id)::text);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER notify_session_change AFTER UPDATE OR DELETE ON agent_sessions_cc
    FOR EACH ROW EXECUTE FUNCTION notify_session_change();

-- Queued messages: the queued count is part of the session state.
CREATE FUNCTION notify_session_input() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM pg_notify('ka_sessions', json_build_object('s', OLD.session_id)::text);
    ELSE
        PERFORM pg_notify('ka_sessions', json_build_object('s', NEW.session_id)::text);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER notify_session_input AFTER INSERT OR UPDATE OR DELETE ON agent_session_inputs
    FOR EACH ROW EXECUTE FUNCTION notify_session_input();

COMMIT;
