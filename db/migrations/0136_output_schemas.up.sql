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
    ADD COLUMN output_schema_id uuid,
    ADD COLUMN output_schema_version integer,
    ADD CONSTRAINT agent_sessions_output_schema_pinned CHECK (output_schema_version IS NULL OR output_schema_id IS NOT NULL);
COMMENT ON COLUMN agent_sessions.output_schema_id IS 'The schema the run was started with (no foreign key: a run outlives its schema).';
COMMENT ON COLUMN agent_sessions.output IS 'The JSON the run finished with, validated against its output schema version.';

COMMIT;
