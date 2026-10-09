BEGIN;
SET search_path = ka, public;

-- Events get a status — the event processor in repository-worker runs every matching handler once, then marks the
-- event processed (or failed when a handler failed); nothing is retried — and a place in a chain: an event a handler
-- emitted points to the event it came from (parent), the chain's first event (root) and how deep it is.
ALTER TABLE events
    ADD COLUMN parent_id bigint REFERENCES events(id) ON DELETE SET NULL,
    ADD COLUMN root_id bigint,
    ADD COLUMN depth integer NOT NULL DEFAULT 0 CONSTRAINT events_depth CHECK (depth BETWEEN 0 AND 5),
    ADD COLUMN handler_id uuid,
    ADD COLUMN status text NOT NULL DEFAULT 'pending' CONSTRAINT events_status CHECK (status IN ('pending', 'processed', 'failed')),
    ADD COLUMN processed_at timestamptz;
-- Events recorded before handlers existed are not replayed through new handlers.
UPDATE events SET root_id = id, status = 'processed', processed_at = created_at;
ALTER TABLE events
    ALTER COLUMN root_id SET NOT NULL,
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira', 'handler')),
    ADD CONSTRAINT events_processed_at CHECK ((status = 'pending') = (processed_at IS NULL)),
    ADD CONSTRAINT events_handler_made CHECK (source <> 'handler' OR handler_id IS NOT NULL),
    ADD CONSTRAINT events_source_made CHECK (source = 'handler' OR (parent_id IS NULL AND handler_id IS NULL AND depth = 0));
COMMENT ON COLUMN events.handler_id IS 'The handler that emitted the event (no foreign key: a deleted handler leaves its events).';
COMMENT ON COLUMN events.parent_id IS 'The event a handler emitted this one from; NULL for a source event or when the parent was deleted.';

-- A root event is its own root.
CREATE FUNCTION events_root() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.root_id IS NULL THEN
        NEW.root_id := NEW.id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER events_root BEFORE INSERT ON events FOR EACH ROW EXECUTE FUNCTION events_root();

CREATE INDEX events_pending ON events(id) WHERE status = 'pending';
CREATE INDEX events_chain ON events(root_id, depth, id);
CREATE INDEX events_by_parent ON events(parent_id);

-- A handler: an event type pattern and field conditions (all must hold), then one action — emit a new event from
-- Jinja templates, or start an agent session whose first message is a Jinja prompt (created_by is its actor).
CREATE TABLE event_handlers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name text NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
    enabled boolean NOT NULL DEFAULT true,
    type_pattern text NOT NULL CHECK (char_length(type_pattern) BETWEEN 1 AND 200),
    conditions jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(conditions) = 'array' AND jsonb_array_length(conditions) <= 20),
    action text NOT NULL CHECK (action IN ('emit', 'agent')),
    emit jsonb CHECK (emit IS NULL OR jsonb_typeof(emit) = 'object'),
    agent_id text,
    prompt text CHECK (char_length(prompt) <= 20000),
    created_by uuid NOT NULL REFERENCES principals(id),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name),
    CONSTRAINT event_handlers_action_fields CHECK (CASE action
        WHEN 'emit' THEN emit IS NOT NULL AND agent_id IS NULL AND prompt IS NULL
        ELSE emit IS NULL AND agent_id IS NOT NULL AND prompt IS NOT NULL END)
);
COMMENT ON COLUMN event_handlers.agent_id IS 'Send to agent: the agent (no foreign key: deleting the agent fails the handler''s runs with Agent not found).';
COMMENT ON COLUMN event_handlers.conditions IS 'Array of {field, op, value}; op in equals, not_equals, contains, in, matches, exists.';

-- What each matching handler did with an event.
CREATE TABLE event_handler_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id bigint NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    handler_id uuid NOT NULL,
    handler_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('succeeded', 'failed')),
    message text NOT NULL DEFAULT '' CHECK (char_length(message) <= 2000),
    emitted_event_id bigint REFERENCES events(id) ON DELETE SET NULL,
    agent_session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX event_handler_runs_by_event ON event_handler_runs(event_id);
CREATE INDEX event_handler_runs_by_handler ON event_handler_runs(handler_id, id DESC);

ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event'));
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent).';

COMMIT;
