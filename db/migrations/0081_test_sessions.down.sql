BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Restores the 0080 schema with the old Test chat tables empty. Test sessions (their inputs and suggestions) are
-- deleted; their run records (cc:<id>:<turn>) stay. Test chats deleted by 0081 do not come back.
DROP TABLE agent_session_reviews;
DELETE FROM agent_sessions_cc WHERE purpose = 'test';  -- inputs cascade
DROP INDEX agent_sessions_cc_agent_purpose;
ALTER TABLE agent_sessions_cc
    DROP CONSTRAINT agent_sessions_cc_documentation,
    DROP CONSTRAINT agent_sessions_cc_test_native,
    DROP COLUMN purpose,
    DROP COLUMN model_calls,
    DROP COLUMN reasoning_tokens,
    ALTER COLUMN document_name SET NOT NULL,
    ALTER COLUMN folder SET NOT NULL;

CREATE TABLE agent_sessions (
    id uuid NOT NULL,
    project_id uuid NOT NULL,
    agent text NOT NULL,
    actor_id uuid NOT NULL,
    title text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    config jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    is_test boolean NOT NULL DEFAULT false,
    history jsonb NOT NULL DEFAULT '[]',
    idle_timeout interval NOT NULL DEFAULT interval '1 hour',
    run_resources jsonb,
    CONSTRAINT agent_test_sessions_pkey PRIMARY KEY (id),
    CONSTRAINT agent_test_sessions_id_project_id_agent_key UNIQUE (id, project_id, agent),
    CONSTRAINT agent_test_sessions_status_check CHECK (status IN ('active', 'closed', 'expired')),
    CONSTRAINT agent_sessions_history_check CHECK (jsonb_typeof(history) = 'array'),
    CONSTRAINT agent_sessions_idle_timeout_check CHECK (idle_timeout > interval '0'),
    CONSTRAINT agent_sessions_run_resources CHECK (run_resources IS NULL OR jsonb_typeof(run_resources) = 'object'),
    CONSTRAINT agent_test_sessions_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id),
    CONSTRAINT agent_test_sessions_agent_fkey FOREIGN KEY (agent) REFERENCES agents(id),
    CONSTRAINT agent_test_sessions_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES principals(id)
);
CREATE INDEX agent_sessions_agent_created ON agent_sessions(agent, created_at DESC);
COMMENT ON COLUMN agent_sessions.run_resources IS 'Per-run agents: the resources this conversation was given (fixed for the conversation).';

CREATE TABLE operations_runs (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    actor_id uuid NOT NULL,
    task text NOT NULL,
    status text NOT NULL DEFAULT 'running',
    context jsonb NOT NULL,
    progress text NOT NULL DEFAULT '',
    answer text NOT NULL DEFAULT '',
    error text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    agent text NOT NULL DEFAULT 'operations',
    session_id uuid,
    run_resources jsonb,
    CONSTRAINT operations_runs_pkey PRIMARY KEY (id),
    CONSTRAINT operations_runs_task_check CHECK (char_length(task) BETWEEN 1 AND 20000),
    CONSTRAINT operations_runs_status_check CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
    CONSTRAINT operations_runs_run_resources CHECK (run_resources IS NULL OR jsonb_typeof(run_resources) = 'object'),
    CONSTRAINT operations_runs_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id),
    CONSTRAINT operations_runs_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES principals(id),
    CONSTRAINT operations_runs_agent_fkey FOREIGN KEY (agent) REFERENCES agents(id),
    CONSTRAINT operations_run_session FOREIGN KEY (session_id, project_id, agent)
        REFERENCES agent_sessions(id, project_id, agent)
);
CREATE INDEX operations_runs_recent ON operations_runs(project_id, created_at DESC);
CREATE INDEX operations_runs_agent_recent ON operations_runs(agent, created_at DESC);
CREATE INDEX operations_runs_session ON operations_runs(session_id, created_at, id);
CREATE UNIQUE INDEX operations_runs_one_active_turn ON operations_runs(session_id)
    WHERE status = 'running' AND session_id IS NOT NULL;
COMMENT ON COLUMN operations_runs.run_resources IS 'Per-run agents: the resources this turn ran with.';

CREATE TABLE operations_commands (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    server_id uuid,
    server_name text NOT NULL,
    hostname text NOT NULL,
    username text NOT NULL,
    command text NOT NULL,
    as_root boolean NOT NULL,
    status text NOT NULL DEFAULT 'running',
    exit_code integer,
    stdout text NOT NULL DEFAULT '',
    stderr text NOT NULL DEFAULT '',
    error text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CONSTRAINT operations_commands_pkey PRIMARY KEY (id),
    CONSTRAINT operations_commands_status_check
        CHECK (status IN ('running', 'completed', 'failed', 'cancelled', 'denied')),
    CONSTRAINT operations_commands_run_id_fkey FOREIGN KEY (run_id) REFERENCES operations_runs(id)
);
CREATE INDEX operations_commands_run ON operations_commands(run_id, created_at);

CREATE TABLE agent_session_reviews (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL,
    actor_id uuid NOT NULL,
    comment text NOT NULL,
    context jsonb NOT NULL,
    suggestion text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_session_reviews_pkey PRIMARY KEY (id),
    CONSTRAINT agent_session_reviews_comment_check CHECK (length(comment) BETWEEN 1 AND 20000),
    CONSTRAINT agent_session_reviews_session_id_fkey FOREIGN KEY (session_id) REFERENCES agent_sessions(id),
    CONSTRAINT agent_session_reviews_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES principals(id)
);
CREATE INDEX agent_session_reviews_session ON agent_session_reviews(session_id, created_at);

COMMIT;
