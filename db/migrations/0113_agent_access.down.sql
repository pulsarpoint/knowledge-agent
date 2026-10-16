BEGIN;
SET search_path = ka, public;

-- The per-type tables still hold every agent's selections (0113 only read them).
ALTER TABLE agents DROP COLUMN access, DROP COLUMN instructions;

COMMIT;
