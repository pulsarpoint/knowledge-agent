SET search_path = ka, public;

-- A Claude Code agent is a registration (name, description, runtime). Model, folders and prompt come with each session.
ALTER TABLE agents ADD COLUMN runtime text NOT NULL DEFAULT 'openai' CHECK (runtime IN ('openai','claude_code'));
ALTER TABLE agents ADD CONSTRAINT claude_code_agent_is_a_registration CHECK (runtime = 'openai' OR (
    NOT is_coordinator AND model_id IS NULL AND active_config_version_id IS NULL
    AND NOT documents_enabled AND NOT documents_grounded AND NOT repositories_enabled AND NOT ssh_enabled
    AND NOT skills_enabled AND NOT mcp_enabled AND NOT services_enabled AND NOT all_collections AND NOT all_repositories));

-- 0022 made a system prompt mandatory for every agent. It stays mandatory for OpenAI agents: the FK now follows a
-- generated column that is NULL for Claude Code agents.
ALTER TABLE agents DROP CONSTRAINT agent_system_prompt_fk;
ALTER TABLE agents ADD COLUMN prompt_agent text GENERATED ALWAYS AS (CASE WHEN runtime = 'openai' THEN id END) STORED;
ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk FOREIGN KEY (prompt_agent) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE agent_sessions_cc (
    id               uuid PRIMARY KEY,
    project_id       uuid NOT NULL REFERENCES projects(id),
    agent_id         text NOT NULL REFERENCES agents(id),
    actor_id         uuid NOT NULL REFERENCES principals(id),
    model_id         uuid NOT NULL REFERENCES llm_models(id),
    title            text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 240),
    prompt_snapshot  jsonb NOT NULL CHECK (jsonb_typeof(prompt_snapshot) = 'object'),
    context_snapshot jsonb NOT NULL CHECK (jsonb_typeof(context_snapshot) = 'object'),
    document_name    text NOT NULL CHECK (char_length(btrim(document_name)) BETWEEN 1 AND 240),
    folder           text NOT NULL CHECK (
        length(folder) <= 200 AND folder ~ '^[a-z0-9_-][a-z0-9._-]*(/[a-z0-9_-][a-z0-9._-]*)*$'
        AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services'),
    status           text NOT NULL DEFAULT 'starting'
        CHECK (status IN ('starting','working','waiting','stopped','failed','closed')),
    status_message   text NOT NULL DEFAULT '' CHECK (char_length(status_message) <= 2000),
    draft            text CHECK (char_length(draft) <= 1000000),
    draft_turn       integer CHECK (draft_turn > 0),
    document_id      uuid REFERENCES documents(id) ON DELETE SET NULL,
    claude_session_id text CHECK (char_length(claude_session_id) <= 200),
    turns            integer NOT NULL DEFAULT 0 CHECK (turns >= 0),
    input_tokens     bigint NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens    bigint NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cached_tokens    bigint NOT NULL DEFAULT 0 CHECK (cached_tokens >= 0),
    cost_usd         numeric(14,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    worker_id        text,
    lease_expires_at timestamptz,
    stop_requested_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz,
    CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);
CREATE INDEX agent_sessions_cc_open ON agent_sessions_cc(created_at) WHERE status <> 'closed';
CREATE INDEX agent_sessions_cc_listing ON agent_sessions_cc(project_id, created_at DESC);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_sessions_cc FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE agent_session_inputs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id   uuid NOT NULL REFERENCES agent_sessions_cc(id) ON DELETE CASCADE,
    seq          integer NOT NULL CHECK (seq > 0),
    actor_id     uuid NOT NULL REFERENCES principals(id),
    text         text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 200000),
    status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','delivered','discarded')),
    turn         integer CHECK (turn > 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    UNIQUE (session_id, seq),
    CHECK ((status = 'delivered') = (turn IS NOT NULL AND delivered_at IS NOT NULL))
);
CREATE INDEX agent_session_inputs_queued ON agent_session_inputs(session_id, seq) WHERE status = 'queued';
