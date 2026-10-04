SET search_path = ka, public;

-- Complete, ordered record of every agent run: instructions, input, reasoning,
-- messages, tool calls with full arguments and outputs, usage and errors.
CREATE TABLE agent_run_events (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    run_key    text NOT NULL CHECK (length(run_key) BETWEEN 1 AND 200),
    seq        integer NOT NULL CHECK (seq > 0),
    kind       text NOT NULL CHECK (kind IN ('start','instructions','input','reasoning','message',
                                             'tool_call','tool_result','usage','error','end')),
    agent_id   text NOT NULL,
    actor_id   uuid REFERENCES principals(id),
    payload    jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_key, seq)
);
CREATE INDEX agent_run_events_starts ON agent_run_events(project_id, created_at DESC) WHERE kind = 'start';
