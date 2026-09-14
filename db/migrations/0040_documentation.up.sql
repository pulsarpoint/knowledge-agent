BEGIN;
SET search_path = ka, public;

CREATE TABLE documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    slug text NOT NULL CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) <= 120),
    current_version_id uuid NOT NULL,
    published_version_id uuid,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id,project_id),
    UNIQUE (project_id,slug)
);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE documentation_prompts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name nonblank NOT NULL CHECK (length(name) <= 160),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
    input_kind text NOT NULL CHECK (input_kind IN ('code','server','service','mixed')),
    enabled boolean NOT NULL DEFAULT true,
    active_version_id uuid,
    next_version integer NOT NULL DEFAULT 1 CHECK (next_version > 0),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id,project_id)
);
CREATE UNIQUE INDEX documentation_prompts_name ON documentation_prompts(project_id,lower(name));
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON documentation_prompts
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TABLE documentation_prompt_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id uuid NOT NULL REFERENCES documentation_prompts(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    content nonblank NOT NULL CHECK (length(content) <= 100000),
    activated_at timestamptz,
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (prompt_id,version),
    UNIQUE (id,prompt_id)
);
ALTER TABLE documentation_prompts ADD CONSTRAINT documentation_prompts_active_version_fk
    FOREIGN KEY (active_version_id,id) REFERENCES documentation_prompt_versions(id,prompt_id);
CREATE FUNCTION guard_documentation_prompt_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW)-'activated_at') IS DISTINCT FROM (to_jsonb(OLD)-'activated_at')
       OR (OLD.activated_at IS NOT NULL AND NEW.activated_at IS DISTINCT FROM OLD.activated_at) THEN
        RAISE EXCEPTION 'Saved prompt content is immutable; create a new draft' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER guard_documentation_prompt_version BEFORE UPDATE ON documentation_prompt_versions
    FOR EACH ROW EXECUTE FUNCTION guard_documentation_prompt_version();

-- One generation may propose versions for several documents. No credentials belong in snapshots.
CREATE TABLE documentation_generations (
    job_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    mode text NOT NULL CHECK (mode IN ('create','update')),
    instructions text NOT NULL,
    agent_snapshot jsonb NOT NULL CHECK (jsonb_typeof(agent_snapshot)='object'),
    prompt_snapshot jsonb NOT NULL CHECK (jsonb_typeof(prompt_snapshot)='array'),
    source_snapshot jsonb NOT NULL CHECK (jsonb_typeof(source_snapshot)='object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (job_id,project_id) REFERENCES jobs(id,project_id),
    UNIQUE (job_id,project_id)
);
CREATE FUNCTION check_documentation_job() RETURNS trigger LANGUAGE plpgsql SET search_path=ka,pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM jobs WHERE id=NEW.job_id AND project_id=NEW.project_id
        AND kind='documentation_generate' AND status='queued') THEN
        RAISE EXCEPTION 'Documentation input must reference a queued documentation job' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_documentation_job BEFORE INSERT ON documentation_generations
    FOR EACH ROW EXECUTE FUNCTION check_documentation_job();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON documentation_generations
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TABLE document_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    title nonblank NOT NULL CHECK (length(title) <= 240),
    description text NOT NULL CHECK (length(description) <= 4000),
    purpose nonblank NOT NULL CHECK (length(purpose) <= 80),
    audience text NOT NULL CHECK (audience IN ('people','agents','both')),
    tags text[] NOT NULL DEFAULT '{}',
    markdown nonblank NOT NULL CHECK (length(markdown) <= 1000000),
    content_hash sha256 NOT NULL CHECK (content_hash=encode(sha256(convert_to(markdown,'UTF8')),'hex')),
    source_snapshot jsonb NOT NULL CHECK (jsonb_typeof(source_snapshot)='object'),
    base_version_id uuid,
    origin text NOT NULL CHECK (origin IN ('manual','generated','uploaded')),
    generation_id uuid,
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((origin='generated') = (generation_id IS NOT NULL)),
    FOREIGN KEY (document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (generation_id,project_id) REFERENCES documentation_generations(job_id,project_id),
    UNIQUE (id,document_id),
    UNIQUE (document_id,version),
    FOREIGN KEY (base_version_id,document_id) REFERENCES document_versions(id,document_id)
);
ALTER TABLE documents ADD CONSTRAINT documents_current_version_fk
    FOREIGN KEY (current_version_id,id) REFERENCES document_versions(id,document_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE documents ADD CONSTRAINT documents_published_version_fk
    FOREIGN KEY (published_version_id,id) REFERENCES document_versions(id,document_id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX document_versions_generation ON document_versions(generation_id) WHERE generation_id IS NOT NULL;
CREATE TRIGGER reject_mutation BEFORE UPDATE ON document_versions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

CREATE TABLE document_code_projects (
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    code_project_id uuid NOT NULL,
    PRIMARY KEY (document_id,code_project_id),
    FOREIGN KEY (document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (code_project_id,project_id) REFERENCES repositories(id,project_id) ON DELETE CASCADE
);
CREATE INDEX document_code_projects_source ON document_code_projects(code_project_id);
CREATE TABLE document_services (
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    service_id uuid NOT NULL,
    PRIMARY KEY (document_id,service_id),
    FOREIGN KEY (document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (service_id,project_id) REFERENCES services(id,project_id) ON DELETE CASCADE
);
CREATE INDEX document_services_source ON document_services(service_id);
CREATE TABLE document_servers (
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    server_id uuid NOT NULL,
    PRIMARY KEY (document_id,server_id),
    FOREIGN KEY (document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id,project_id) REFERENCES servers(id,project_id) ON DELETE CASCADE
);
CREATE INDEX document_servers_source ON document_servers(server_id);
-- Preserve document links when deployment IDs survive a service edit, including swapped names/environments.
ALTER TABLE service_deployments DROP CONSTRAINT service_deployments_service_id_server_id_environment_runtim_key;
ALTER TABLE service_deployments ADD CONSTRAINT service_deployments_service_id_server_id_environment_runtim_key
    UNIQUE (service_id,server_id,environment,runtime_name) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE service_deployments ADD CONSTRAINT service_deployments_id_project_key UNIQUE (id,project_id);
CREATE TABLE document_deployments (
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    deployment_id uuid NOT NULL,
    PRIMARY KEY (document_id,deployment_id),
    FOREIGN KEY (document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (deployment_id,project_id) REFERENCES service_deployments(id,project_id) ON DELETE CASCADE
);
CREATE INDEX document_deployments_source ON document_deployments(deployment_id);

CREATE VIEW document_details AS
SELECT d.*, v.version, v.title, v.description, v.purpose, v.audience, v.tags, v.content_hash,
    COALESCE((SELECT array_agg(code_project_id ORDER BY code_project_id) FROM document_code_projects WHERE document_id=d.id),'{}') AS code_project_ids,
    COALESCE((SELECT array_agg(service_id ORDER BY service_id) FROM document_services WHERE document_id=d.id),'{}') AS service_ids,
    COALESCE((SELECT array_agg(server_id ORDER BY server_id) FROM document_servers WHERE document_id=d.id),'{}') AS server_ids,
    COALESCE((SELECT array_agg(deployment_id ORDER BY deployment_id) FROM document_deployments WHERE document_id=d.id),'{}') AS deployment_ids
FROM documents d JOIN document_versions v ON v.id=d.current_version_id;
COMMENT ON TABLE documents IS 'Managed documents. Canonical Markdown is in immutable document_versions; RAG publishing is a separate operation.';
COMMENT ON COLUMN document_versions.source_snapshot IS 'Names and inventory references at save time, retained even if linked inventory is removed. Never store credentials.';
COMMIT;
