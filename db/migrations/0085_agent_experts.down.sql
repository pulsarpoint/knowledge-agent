BEGIN;
SET search_path = ka, public;

DROP INDEX agent_run_events_expert_calls;

DELETE FROM agent_tools WHERE tool LIKE 'expert:%';
ALTER TABLE agent_tools DROP CONSTRAINT subagents_do_not_spawn;
ALTER TABLE agent_tools ADD CONSTRAINT subagents_do_not_spawn
    CHECK (NOT subagent_allowed OR (tool <> 'run_subagent' AND tool NOT LIKE 'ask_agent:%'));
COMMENT ON COLUMN agent_tools.tool IS NULL;
ALTER TABLE agents DROP COLUMN expert;

COMMIT;
