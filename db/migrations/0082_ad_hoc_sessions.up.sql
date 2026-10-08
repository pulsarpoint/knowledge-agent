BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Generate documents sessions run an ad-hoc native agent pinned in agent_config: no agents row. Test sessions and
-- Claude Code sessions keep their agent.
ALTER TABLE agent_sessions_cc
    ALTER COLUMN agent_id DROP NOT NULL,
    ADD CONSTRAINT agent_sessions_cc_ad_hoc CHECK (agent_id IS NOT NULL
        OR (purpose = 'documents' AND runtime = 'openai' AND agent_config IS NOT NULL));
COMMENT ON COLUMN agent_sessions_cc.agent_id IS
    'The saved agent; NULL for a Generate documents session, which runs the ad-hoc agent pinned in agent_config.';

COMMIT;
