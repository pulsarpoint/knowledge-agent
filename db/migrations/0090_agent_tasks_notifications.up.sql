BEGIN;
SET search_path = ka, public;

-- What event-triggered agent calls produced: the agent's answer, or why the call failed. One list for every user
-- (no recipients, no read state); agent_tasks.py in agent-session-worker writes it.
CREATE TABLE notifications (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL CHECK (kind IN ('agent.response')),
    status text NOT NULL CHECK (status IN ('succeeded', 'failed')),
    title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 500),
    body text NOT NULL CHECK (char_length(body) <= 200000),
    event_id bigint REFERENCES events(id) ON DELETE SET NULL,
    handler_id uuid,
    agent_id text,
    agent_task_id text
);
CREATE INDEX notifications_newest ON notifications(project_id, id DESC);
COMMENT ON COLUMN notifications.agent_task_id IS 'The agent task that wrote it (no foreign key: the task points at its notification).';

-- One agent call a Send-to-agent handler queued for an event. The event processor inserts it (queued);
-- agent-session-worker claims it with a lease, runs the agent once without streaming and finishes it with a
-- notification. 3 attempts in all; a task whose lease ran out is claimed again.
CREATE TABLE agent_tasks (
    id text PRIMARY KEY CHECK (id ~ '^agent-task:[0-9]+:[0-9a-f-]{36}$'),
    project_id uuid NOT NULL REFERENCES projects(id),
    agent_id text NOT NULL,
    agent_name text NOT NULL,
    title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 500),
    prompt text NOT NULL CHECK (char_length(prompt) BETWEEN 1 AND 20000),
    event_id bigint REFERENCES events(id) ON DELETE SET NULL,
    handler_id uuid,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    worker_id text,
    lease_expires_at timestamptz,
    error text CHECK (char_length(error) <= 2000),
    notification_id bigint REFERENCES notifications(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    CONSTRAINT agent_tasks_lease CHECK ((status = 'running') = (lease_expires_at IS NOT NULL)),
    CONSTRAINT agent_tasks_finished CHECK ((status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL))
);
COMMENT ON COLUMN agent_tasks.id IS 'agent-task:<event id>:<handler id> — processing an event again queues nothing new.';
COMMENT ON COLUMN agent_tasks.agent_id IS 'No foreign key, like event_handlers.agent_id: a deleted agent fails the task.';
CREATE INDEX agent_tasks_due ON agent_tasks(next_attempt_at, id) WHERE status IN ('queued', 'running');

ALTER TABLE event_handler_runs ADD COLUMN agent_task_id text;
COMMENT ON COLUMN event_handler_runs.agent_task_id IS 'Send to agent: the task it queued (agent_session_id is for runs before 0090).';

COMMIT;
