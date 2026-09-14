BEGIN;
-- The former schema cannot retain live sessions or database-only SDK history.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM ka.agent_sessions WHERE NOT is_test OR history <> '[]'::jsonb) THEN
        RAISE EXCEPTION 'Cannot downgrade populated agent sessions: preserve their history before restoring the old schema.';
    END IF;
END $$;
ALTER TABLE ka.agent_sessions DROP COLUMN history;
ALTER TABLE ka.agent_sessions DROP COLUMN is_test;
ALTER INDEX ka.agent_sessions_agent_created RENAME TO agent_test_sessions_agent_created;
ALTER TABLE ka.agent_sessions RENAME TO agent_test_sessions;
COMMIT;
