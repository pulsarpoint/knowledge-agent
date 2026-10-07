SET search_path = ka, public;

-- The Reader application is removed until it is redesigned: delete its conversations
-- (admin agent sessions are kept) and the schema that only it used.
DELETE FROM agent_run_events e USING operations_runs r, agent_sessions s
    WHERE e.run_key = 'run:' || r.id AND r.session_id = s.id AND s.channel = 'reader';
DELETE FROM operations_commands c USING operations_runs r, agent_sessions s
    WHERE c.run_id = r.id AND r.session_id = s.id AND s.channel = 'reader';
DELETE FROM operations_runs r USING agent_sessions s WHERE r.session_id = s.id AND s.channel = 'reader';
DELETE FROM agent_session_reviews v USING agent_sessions s WHERE v.session_id = s.id AND s.channel = 'reader';
DELETE FROM agent_sessions WHERE channel = 'reader';

DROP TABLE reader_settings;
DROP INDEX agent_sessions_reader_owner;
ALTER TABLE agent_sessions DROP COLUMN channel, DROP COLUMN hidden_by_owner_at;
