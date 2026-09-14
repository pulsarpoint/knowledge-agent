DROP TABLE ka.agent_session_reviews;
ALTER TABLE ka.operations_runs DROP CONSTRAINT operations_run_session;
ALTER TABLE ka.operations_runs DROP COLUMN session_id;
DROP TABLE ka.agent_test_sessions;
