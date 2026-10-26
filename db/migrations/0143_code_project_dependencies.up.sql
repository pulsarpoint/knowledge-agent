BEGIN;
SET search_path = ka, public;

-- A code project depends on another code project (a repository that imports a library repository). Services relate to
-- code only through built_from; the libraries a service uses and the services a library affects are derived. Cycles
-- are allowed (they happen in real code); no transitive closure is stored. Drafts like the other inventory tables.
CREATE TABLE code_project_dependencies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    from_code_project_id uuid NOT NULL,
    to_code_project_id uuid NOT NULL,
    description text NOT NULL DEFAULT ''
        CONSTRAINT code_project_dependencies_description CHECK (length(description) <= 4000),
    status text NOT NULL DEFAULT 'confirmed'
        CONSTRAINT code_project_dependencies_status CHECK (status IN ('draft', 'confirmed')),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_by_agent text REFERENCES agents(id) ON DELETE SET NULL,
    session_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, project_id),
    CONSTRAINT code_project_dependencies_not_self CHECK (from_code_project_id <> to_code_project_id),
    CONSTRAINT code_project_dependencies_once UNIQUE (from_code_project_id, to_code_project_id),
    FOREIGN KEY (from_code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE
);
CREATE INDEX code_project_dependencies_to ON code_project_dependencies (to_code_project_id);
CREATE INDEX code_project_dependencies_project ON code_project_dependencies (project_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON code_project_dependencies
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE code_project_dependencies IS 'A code project depends on another code project (a library it imports).';
COMMENT ON COLUMN code_project_dependencies.status IS 'draft: written by an agent, waiting for a person to confirm or discard it; confirmed: real.';
COMMENT ON COLUMN code_project_dependencies.session_id IS 'ka.agent_sessions.id of the run that created it (no foreign key: the trace outlives the session).';

COMMIT;
