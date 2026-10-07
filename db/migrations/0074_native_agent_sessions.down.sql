BEGIN;
SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_sessions_cc WHERE runtime = 'openai') THEN
        RAISE EXCEPTION 'Native agent sessions exist and 0073 cannot keep them; delete them (ka.agent_sessions_cc WHERE runtime = ''openai'', with their cc:<session>:* run records) before rolling back 0074';
    END IF;
END $$;

ALTER TABLE agent_sessions_cc
    DROP CONSTRAINT agent_sessions_cc_native_config,
    DROP COLUMN history,
    DROP COLUMN agent_config,
    DROP COLUMN runtime;

COMMIT;
