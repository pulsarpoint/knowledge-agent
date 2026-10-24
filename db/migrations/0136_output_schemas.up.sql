BEGIN;
SET search_path = ka, public;

-- The output schema library: a named JSON Schema a run may be asked to answer with. Every save of a changed schema adds
-- a version; old versions are kept because runs and consumers depend on the exact shape they were started with.
CREATE TABLE output_schemas (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
    description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 2000),
    current_version integer NOT NULL DEFAULT 1 CHECK (current_version > 0),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

CREATE TABLE output_schema_versions (
    schema_id uuid NOT NULL REFERENCES output_schemas(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    schema jsonb NOT NULL CHECK (jsonb_typeof(schema) = 'object'),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (schema_id, version)
);

-- A handler that starts a run with a schema; no version means the current one when the run starts. Deletion of a used
-- schema is refused by the API; RESTRICT is the safety net.
ALTER TABLE event_handlers
    ADD COLUMN output_schema_id uuid REFERENCES output_schemas(id) ON DELETE RESTRICT,
    ADD COLUMN output_schema_version integer,
    ADD CONSTRAINT event_handlers_output_schema_version
        FOREIGN KEY (output_schema_id, output_schema_version) REFERENCES output_schema_versions(schema_id, version) ON DELETE RESTRICT,
    ADD CONSTRAINT event_handlers_output_schema_pinned CHECK (output_schema_version IS NULL OR output_schema_id IS NOT NULL);

-- A run that was started with a schema: the version it pinned and the validated JSON it finished with.
ALTER TABLE agent_sessions
    ADD COLUMN output jsonb,
    ADD COLUMN output_error jsonb CHECK (output_error IS NULL OR jsonb_typeof(output_error) = 'object'),
    ADD COLUMN output_schema_id uuid,
    ADD COLUMN output_schema_version integer,
    ADD CONSTRAINT agent_sessions_output_schema_pinned CHECK (output_schema_version IS NULL OR output_schema_id IS NOT NULL);
COMMENT ON COLUMN agent_sessions.output_schema_id IS 'The schema the run was started with (no foreign key: a run outlives its schema).';
COMMENT ON COLUMN agent_sessions.output_error IS 'A final answer that did not match its schema: {errors, raw}.';
COMMENT ON COLUMN agent_sessions.output IS 'The JSON the run finished with, validated against its output schema version.';

-- A run that finished with a valid output emits run.output (source 'run', source_id: the run). A run a handler started
-- from an event records it one level below that event, in its chain (so MAX_DEPTH stops every loop).
ALTER TABLE events
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira', 'handler', 'manual', 'memory', 'schedule', 'run')),
    DROP CONSTRAINT events_source_made,
    ADD CONSTRAINT events_source_made CHECK (
        source = 'handler' OR (handler_id IS NULL AND (source = 'run' OR (parent_id IS NULL AND depth = 0)))
    );

-- Model capabilities (LLM settings): supports_structured_output (strict JSON Schema output); on for OpenAI-style models.
ALTER TABLE llm_models
    ADD COLUMN capabilities jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(capabilities) = 'object');
UPDATE llm_models SET capabilities = jsonb_build_object('supports_structured_output', provider = 'openai');

-- A handler that does not start its agent because a run.output chain is too deep is recorded as skipped.
ALTER TABLE event_handler_runs
    DROP CONSTRAINT event_handler_runs_status_check,
    ADD CONSTRAINT event_handler_runs_status_check CHECK (status IN ('succeeded', 'failed', 'skipped'));

COMMIT;
