BEGIN;
SET search_path = ka, public;

-- 0115: every run gets every tool (the whole catalog, its MCP servers' tools, every expert), so the per-agent tool
-- rows (on/off, usage overrides, subagent_allowed, the run_subagent switch, a caller's expert choices) and the
-- catalog's needs go.
DROP TABLE agent_tools;
ALTER TABLE tools DROP COLUMN needs;

COMMIT;
