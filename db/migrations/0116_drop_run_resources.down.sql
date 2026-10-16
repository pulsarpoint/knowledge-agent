BEGIN;
SET search_path = ka, public;

-- The column comes back empty (NULL), as 0076 added it; what runs were given is not restored.
ALTER TABLE agent_sessions ADD COLUMN run_resources jsonb
    CONSTRAINT agent_sessions_run_resources CHECK (run_resources IS NULL OR jsonb_typeof(run_resources) = 'object');
COMMENT ON COLUMN agent_sessions.run_resources IS 'Per-run agents: the resources this conversation was given (fixed for the conversation).';

COMMIT;
