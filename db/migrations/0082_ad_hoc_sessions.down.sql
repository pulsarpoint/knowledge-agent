BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Ad-hoc sessions have no agent to fall back to: they are deleted (inputs cascade; run records stay).
DELETE FROM agent_sessions_cc WHERE agent_id IS NULL;
ALTER TABLE agent_sessions_cc
    DROP CONSTRAINT agent_sessions_cc_ad_hoc,
    ALTER COLUMN agent_id SET NOT NULL;
COMMENT ON COLUMN agent_sessions_cc.agent_id IS NULL;

COMMIT;
