BEGIN;
SET search_path = ka, public;

-- What each run of a per-run agent was given (code projects, collections, servers and services, by id), on the
-- record of the run: Generate documents sessions, test chats and their turns. NULL for runs of fixed agents.
ALTER TABLE agent_sessions_cc ADD COLUMN run_resources jsonb
    CONSTRAINT agent_sessions_cc_run_resources CHECK (run_resources IS NULL OR jsonb_typeof(run_resources) = 'object');
ALTER TABLE agent_sessions ADD COLUMN run_resources jsonb
    CONSTRAINT agent_sessions_run_resources CHECK (run_resources IS NULL OR jsonb_typeof(run_resources) = 'object');
ALTER TABLE operations_runs ADD COLUMN run_resources jsonb
    CONSTRAINT operations_runs_run_resources CHECK (run_resources IS NULL OR jsonb_typeof(run_resources) = 'object');
COMMENT ON COLUMN agent_sessions_cc.run_resources IS 'Per-run agents: the code projects, collections, servers and services this session was given.';
COMMENT ON COLUMN agent_sessions.run_resources IS 'Per-run agents: the resources this conversation was given (fixed for the conversation).';
COMMENT ON COLUMN operations_runs.run_resources IS 'Per-run agents: the resources this turn ran with.';

COMMIT;
