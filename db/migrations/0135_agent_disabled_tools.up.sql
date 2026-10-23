BEGIN;
SET search_path = ka, public;

-- The catalog tools switched off for an agent: never offered in any run of it. Off tools are stored, so a tool added
-- to the catalog later is on for every agent.
ALTER TABLE agents ADD COLUMN disabled_tools text[] NOT NULL DEFAULT '{}';
COMMENT ON COLUMN agents.disabled_tools IS 'Catalog tool names never offered in a run of this agent; a run may switch more off.';

COMMIT;
