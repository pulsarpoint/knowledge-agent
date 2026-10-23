BEGIN;
SET search_path = ka, public;

-- Back to 0134: every agent is offered every catalog tool.
ALTER TABLE agents DROP COLUMN disabled_tools;

COMMIT;
