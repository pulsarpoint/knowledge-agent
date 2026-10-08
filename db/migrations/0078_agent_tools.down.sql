BEGIN;
SET search_path = ka, public;

-- 0077 derives every native agent's tools from its capabilities again; edited usage instructions are dropped.
DROP TABLE agent_tools;

COMMIT;
