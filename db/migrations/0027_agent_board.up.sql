BEGIN;
CREATE TABLE ka.agent_board_tasks (
    id uuid PRIMARY KEY,
    agent_id text NOT NULL REFERENCES ka.agents(id),
    agent_name text NOT NULL,
    task text NOT NULL CHECK (length(btrim(task)) BETWEEN 1 AND 40000),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed','cancelled')),
    cancel_requested boolean NOT NULL DEFAULT false,
    config jsonb,
    model_name text,
    prompt_version integer,
    result text,
    error text,
    usage jsonb,
    created_by uuid NOT NULL REFERENCES ka.principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    CHECK ((status IN ('completed','failed','cancelled')) = (finished_at IS NOT NULL))
);
CREATE INDEX agent_board_tasks_queue ON ka.agent_board_tasks(created_at,id) WHERE status='queued';
CREATE INDEX agent_board_tasks_history ON ka.agent_board_tasks(created_at DESC,id);
COMMENT ON COLUMN ka.agent_board_tasks.config IS 'Configuration used by this run, with credential references only; never decrypted credentials.';
COMMIT;
