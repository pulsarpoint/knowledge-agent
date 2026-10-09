BEGIN;
SET search_path = ka, public;

-- Things that happened at a source, kept as a queue for later handling (no status yet). The scheduled Jira pull
-- records jira.ticket.created / jira.ticket.updated; dedupe_key makes a repeated pull record nothing new.
CREATE TABLE events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    source text NOT NULL CHECK (source IN ('jira')),
    source_id uuid,
    type text NOT NULL CHECK (type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
    subject nonblank NOT NULL,
    title text NOT NULL DEFAULT '',
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    dedupe_key nonblank NOT NULL,
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, dedupe_key)
);
COMMENT ON COLUMN events.source_id IS 'The source''s row, e.g. jira_connections.id (no foreign key: sources live in different tables).';
CREATE INDEX events_newest ON events(project_id, id DESC);
CREATE INDEX events_by_type ON events(project_id, type, id DESC);
-- The Jira pull compares each ticket with its latest event from the same connection.
CREATE INDEX events_jira_issues ON events(project_id, source_id, (payload->>'issue_id'), id DESC) WHERE source = 'jira';

COMMIT;
