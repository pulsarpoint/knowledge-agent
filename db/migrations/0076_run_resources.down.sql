BEGIN;
SET search_path = ka, public;

-- Only the records go: pinned configurations (agent_sessions.config, agent_sessions_cc.agent_config) keep their
-- narrowed lists and run_resources, so no conversation gains access.
ALTER TABLE operations_runs DROP COLUMN run_resources;
ALTER TABLE agent_sessions DROP COLUMN run_resources;
ALTER TABLE agent_sessions_cc DROP COLUMN run_resources;

COMMIT;
