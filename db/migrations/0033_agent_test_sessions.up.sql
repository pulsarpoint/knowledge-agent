CREATE TABLE ka.agent_test_sessions (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES ka.projects(id),
    agent text NOT NULL REFERENCES ka.agents(id),
    actor_id uuid NOT NULL REFERENCES ka.principals(id),
    title text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed','expired')),
    config jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    UNIQUE (id,project_id,agent)
);
CREATE INDEX agent_test_sessions_agent_created ON ka.agent_test_sessions(agent,created_at DESC);

ALTER TABLE ka.operations_runs ADD COLUMN session_id uuid;
ALTER TABLE ka.operations_runs ADD CONSTRAINT operations_run_session
    FOREIGN KEY (session_id,project_id,agent) REFERENCES ka.agent_test_sessions(id,project_id,agent);
CREATE INDEX operations_runs_session ON ka.operations_runs(session_id,created_at,id);
CREATE UNIQUE INDEX operations_runs_one_active_turn ON ka.operations_runs(session_id)
    WHERE status='running' AND session_id IS NOT NULL;

CREATE TABLE ka.agent_session_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES ka.agent_test_sessions(id),
    actor_id uuid NOT NULL REFERENCES ka.principals(id),
    comment text NOT NULL CHECK (length(comment) BETWEEN 1 AND 20000),
    context jsonb NOT NULL,
    suggestion text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_session_reviews_session ON ka.agent_session_reviews(session_id,created_at);
