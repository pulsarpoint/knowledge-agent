BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Deleted Claude Code sessions are not restored; every agent and session comes back as native.
ALTER TABLE agent_sessions DROP CONSTRAINT agent_sessions_ad_hoc;
ALTER TABLE agent_sessions RENAME TO agent_sessions_cc;
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'ka.agent_sessions_cc'::regclass AND conname LIKE 'agent\_sessions\_%'
               AND conname NOT LIKE 'agent\_sessions\_cc\_%' LOOP
        EXECUTE format('ALTER TABLE ka.agent_sessions_cc RENAME CONSTRAINT %I TO %I',
                       r.conname, 'agent_sessions_cc_' || substr(r.conname, 16));
    END LOOP;
    FOR r IN SELECT indexname FROM pg_indexes
             WHERE schemaname = 'ka' AND tablename = 'agent_sessions_cc' AND indexname LIKE 'agent\_sessions\_%'
               AND indexname NOT LIKE 'agent\_sessions\_cc\_%' LOOP
        EXECUTE format('ALTER INDEX ka.%I RENAME TO %I', r.indexname, 'agent_sessions_cc_' || substr(r.indexname, 16));
    END LOOP;
END $$;

ALTER TABLE agent_sessions_cc
    ALTER COLUMN agent_config DROP NOT NULL,
    ADD COLUMN claude_session_id text CONSTRAINT agent_sessions_cc_claude_session_id_check
        CHECK (char_length(claude_session_id) <= 200),
    ADD COLUMN cost_usd numeric(14,6) NOT NULL DEFAULT 0 CONSTRAINT agent_sessions_cc_cost_usd_check CHECK (cost_usd >= 0),
    ADD COLUMN runtime text NOT NULL DEFAULT 'openai',
    ADD CONSTRAINT agent_sessions_cc_runtime CHECK (runtime IN ('openai', 'claude_code')),
    ADD CONSTRAINT agent_sessions_cc_native_config CHECK ((runtime = 'openai') = (agent_config IS NOT NULL)),
    ADD CONSTRAINT agent_sessions_cc_test_native CHECK (purpose = 'documents' OR runtime = 'openai'),
    ADD CONSTRAINT agent_sessions_cc_ad_hoc CHECK (agent_id IS NOT NULL
        OR (purpose = 'documents' AND runtime = 'openai' AND agent_config IS NOT NULL));
ALTER TABLE agent_sessions_cc ALTER COLUMN runtime SET DEFAULT 'claude_code';  -- as 0074 left it
COMMENT ON COLUMN agent_sessions_cc.runtime IS 'The agent type the session runs with: openai (native) or claude_code.';

ALTER TABLE agents
    ADD COLUMN runtime text NOT NULL DEFAULT 'openai' CONSTRAINT agents_runtime_check
        CHECK (runtime IN ('openai', 'claude_code')),
    ADD COLUMN claude_code_tools text[] NOT NULL DEFAULT '{Read,Grep,Glob,Bash}' CONSTRAINT claude_code_tools_known
        CHECK (claude_code_tools <@ ARRAY['Read', 'Grep', 'Glob', 'Bash']);

COMMIT;
