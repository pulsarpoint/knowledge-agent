BEGIN;
SET search_path = ka, public;

-- An expert is a saved agent other agents call as a tool (ask_<name>); its When to use text is the tool's description.
ALTER TABLE agents ADD COLUMN expert boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN agents.expert IS 'Offered as an expert: every other agent may call it as a tool unless it switched it off.';

-- A caller's choice for one expert: 'expert:<agent id>' with mode off (no row: on). Experts are never given to
-- subagents; the unused ask_agent:<id> form is replaced.
ALTER TABLE agent_tools DROP CONSTRAINT subagents_do_not_spawn;
ALTER TABLE agent_tools ADD CONSTRAINT subagents_do_not_spawn
    CHECK (NOT subagent_allowed OR (tool <> 'run_subagent' AND tool NOT LIKE 'expert:%'));
COMMENT ON COLUMN agent_tools.tool IS 'A catalog name (read_file), mcp:<mcp_server_id>:<tool name> or expert:<agent id>.';

COMMIT;
