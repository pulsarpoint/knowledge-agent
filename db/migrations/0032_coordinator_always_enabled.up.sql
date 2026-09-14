BEGIN;
UPDATE ka.agents SET enabled=true,revision=revision+1 WHERE is_coordinator AND NOT enabled;
ALTER TABLE ka.agents ADD CONSTRAINT coordinator_always_enabled CHECK (NOT is_coordinator OR enabled);
COMMIT;
