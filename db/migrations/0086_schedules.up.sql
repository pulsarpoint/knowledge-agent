BEGIN;
SET search_path = ka, public;

-- A schedule runs one target (a named job, e.g. the Jira pull of one connection) on a cron timetable in a time zone.
-- repository-worker claims due rows with a lease; next_run_at is NULL while paused unless Run now is pending.
CREATE TABLE schedules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name nonblank NOT NULL,
    target_kind text NOT NULL CHECK (target_kind IN ('jira.pull')),
    target_id uuid NOT NULL,
    cron text NOT NULL,
    timezone text NOT NULL DEFAULT 'UTC',
    enabled boolean NOT NULL DEFAULT true,
    next_run_at timestamptz,
    run_requested boolean NOT NULL DEFAULT false,
    lease_expires_at timestamptz,
    last_run_at timestamptz,
    last_finished_at timestamptz,
    last_status text CHECK (last_status IN ('succeeded', 'failed', 'skipped')),
    last_message text,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, target_kind, target_id)
);
COMMENT ON COLUMN schedules.target_id IS 'The target''s row, e.g. jira_connections.id for jira.pull (no foreign key: targets live in different tables).';
COMMENT ON COLUMN schedules.run_requested IS 'Run now is pending; its history row gets trigger manual.';
CREATE INDEX schedules_due ON schedules(next_run_at) WHERE next_run_at IS NOT NULL;

-- The newest 100 runs of each schedule (the worker deletes older ones).
CREATE TABLE schedule_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    schedule_id uuid NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    trigger text NOT NULL CHECK (trigger IN ('schedule', 'manual')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('succeeded', 'failed', 'skipped')),
    message text NOT NULL DEFAULT ''
);
CREATE INDEX schedule_runs_newest ON schedule_runs(schedule_id, id DESC);

COMMIT;
