BEGIN;
SET search_path = ka, public;

-- Generate documents sessions for both agent types (knowledge agents step 3). Claude Code sessions run in
-- claude-code-worker as before; native sessions run in agent-session-worker with the agent config pinned when the
-- session started and their Agents SDK conversation history.
ALTER TABLE agent_sessions_cc
    ADD COLUMN runtime text NOT NULL DEFAULT 'claude_code'
        CONSTRAINT agent_sessions_cc_runtime CHECK (runtime IN ('openai','claude_code')),
    ADD COLUMN agent_config jsonb
        CONSTRAINT agent_sessions_cc_agent_config CHECK (agent_config IS NULL OR jsonb_typeof(agent_config) = 'object'),
    ADD COLUMN history jsonb NOT NULL DEFAULT '[]'
        CONSTRAINT agent_sessions_cc_history CHECK (jsonb_typeof(history) = 'array'),
    ADD CONSTRAINT agent_sessions_cc_native_config CHECK ((runtime = 'openai') = (agent_config IS NOT NULL));
COMMENT ON COLUMN agent_sessions_cc.runtime IS 'The agent type the session runs with: openai (native) or claude_code.';
COMMENT ON COLUMN agent_sessions_cc.agent_config IS
    'Native sessions: the agent config pinned when the session started, with the session''s working folders as code roots. Credential references only.';
COMMENT ON COLUMN agent_sessions_cc.history IS
    'Native sessions: the Agents SDK items of successful turns, redacted.';

COMMIT;
