BEGIN;
SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agents WHERE resources = 'per_run')
       OR EXISTS (SELECT 1 FROM agent_versions WHERE config->>'resources' = 'per_run') THEN
        RAISE EXCEPTION 'Per-run agents or per-run configuration versions exist, and 0074 would run them with their whole selection; set the agents to Fixed and delete those versions (ka.agent_versions WHERE config->>''resources'' = ''per_run'') before rolling back 0075';
    END IF;
END $$;

ALTER TABLE agents
    DROP CONSTRAINT per_run_agent_is_not_the_coordinator,
    DROP COLUMN resources;

COMMIT;
