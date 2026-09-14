BEGIN;
ALTER TABLE ka.agents DROP CONSTRAINT coordinator_always_enabled;
COMMIT;
