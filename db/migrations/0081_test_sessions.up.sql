BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Test chats become sessions run by agent-session-worker, like Generate documents sessions (purpose 'test').
-- The old Test chats and their suggestions are deleted with their tables; their run records (run:<id> keys in
-- agent_run_events) stay, so Activity → Agent runs still shows them.
DROP TABLE agent_session_reviews;
DROP TABLE operations_commands;
DROP TABLE operations_runs;
DROP TABLE agent_sessions;

ALTER TABLE agent_sessions_cc
    ADD COLUMN purpose text NOT NULL DEFAULT 'documents'
        CONSTRAINT agent_sessions_cc_purpose CHECK (purpose IN ('documents', 'test')),
    ADD COLUMN model_calls integer NOT NULL DEFAULT 0
        CONSTRAINT agent_sessions_cc_model_calls CHECK (model_calls >= 0),
    ADD COLUMN reasoning_tokens integer NOT NULL DEFAULT 0
        CONSTRAINT agent_sessions_cc_reasoning_tokens CHECK (reasoning_tokens >= 0),
    ALTER COLUMN document_name DROP NOT NULL,
    ALTER COLUMN folder DROP NOT NULL,
    ADD CONSTRAINT agent_sessions_cc_documentation CHECK (CASE purpose
        WHEN 'documents' THEN document_name IS NOT NULL AND folder IS NOT NULL
        ELSE document_name IS NULL AND folder IS NULL AND draft IS NULL AND draft_turn IS NULL AND document_id IS NULL
    END),
    ADD CONSTRAINT agent_sessions_cc_test_native CHECK (purpose = 'documents' OR runtime = 'openai');
CREATE INDEX agent_sessions_cc_agent_purpose ON agent_sessions_cc(agent_id, purpose, created_at DESC);
COMMENT ON COLUMN agent_sessions_cc.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session (native only).';
COMMENT ON COLUMN agent_sessions_cc.model_calls IS 'Native sessions: model calls made, one per call.';
COMMENT ON COLUMN agent_sessions_cc.reasoning_tokens IS 'Native sessions: reasoning tokens reported by the model calls.';

CREATE TABLE agent_session_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES agent_sessions_cc(id) ON DELETE CASCADE,
    actor_id uuid NOT NULL REFERENCES principals(id),
    comment text NOT NULL CHECK (char_length(comment) BETWEEN 1 AND 20000),
    context jsonb NOT NULL CHECK (jsonb_typeof(context) = 'object'),
    suggestion text NOT NULL,
    response_turn integer CHECK (response_turn > 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_session_reviews_session ON agent_session_reviews(session_id, created_at);
COMMENT ON TABLE agent_session_reviews IS
    'Tuning suggestions for Test sessions: the feedback, the reviewer''s context (redacted) and its proposed texts.';

COMMIT;
