BEGIN;
SET search_path = ka, public;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM jobs WHERE kind='documentation_generate') THEN
        RAISE EXCEPTION 'New documentation generation history exists; rollback would lose its meaning';
    END IF;
END $$;
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check, DROP CONSTRAINT jobs_check1;
CREATE TABLE document_sets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    slug text NOT NULL CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    name nonblank NOT NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    archived_at timestamptz,
    UNIQUE (id, project_id),
    UNIQUE (project_id, slug)
);

-- One documentation set can describe several service repositories.
CREATE TABLE document_set_repositories (
    project_id uuid NOT NULL,
    document_set_id uuid NOT NULL,
    repository_id uuid NOT NULL,
    PRIMARY KEY (document_set_id, repository_id),
    FOREIGN KEY (document_set_id, project_id) REFERENCES document_sets(id, project_id),
    FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id)
);
CREATE INDEX document_set_repositories_repo ON document_set_repositories(repository_id);

CREATE TABLE rag_collections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name nonblank NOT NULL,
    kind text NOT NULL CHECK (kind IN ('docs', 'code', 'memory')),
    document_set_id uuid,
    repository_id uuid,
    active_generation_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((kind IN ('docs', 'memory') AND document_set_id IS NOT NULL AND repository_id IS NULL)
        OR (kind = 'code' AND repository_id IS NOT NULL AND document_set_id IS NULL)),
    FOREIGN KEY (document_set_id, project_id) REFERENCES document_sets(id, project_id),
    FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id),
    UNIQUE (id, project_id),
    UNIQUE (id, project_id, document_set_id),
    UNIQUE (project_id, name),
    UNIQUE (document_set_id),
    UNIQUE (repository_id)
);

ALTER TABLE jobs ADD COLUMN document_set_id uuid, ADD COLUMN rag_collection_id uuid;
ALTER TABLE jobs ADD FOREIGN KEY (document_set_id,project_id) REFERENCES document_sets(id,project_id),
    ADD FOREIGN KEY (rag_collection_id,project_id) REFERENCES rag_collections(id,project_id),
    ADD UNIQUE (id,project_id,document_set_id), ADD UNIQUE (id,project_id,rag_collection_id);
CREATE UNIQUE INDEX jobs_one_document_generation ON jobs(document_set_id)
    WHERE status IN ('queued','running','waiting_input');
CREATE UNIQUE INDEX jobs_one_rag_build ON jobs(rag_collection_id)
    WHERE status IN ('queued','running','waiting_input');
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN ('repository_sync', 'documentation_generate', 'rag_index', 'jira_sync', 'server_test', 'rag_folder_index'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check CHECK (num_nonnulls(repository_id, document_set_id, rag_collection_id, jira_connection_id, server_id, rag_folder_id) = 1);
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    (kind = 'repository_sync' AND repository_id IS NOT NULL) OR
    (kind = 'documentation_generate' AND document_set_id IS NOT NULL) OR
    (kind = 'rag_index' AND rag_collection_id IS NOT NULL) OR
    (kind = 'jira_sync' AND jira_connection_id IS NOT NULL) OR
    (kind = 'server_test' AND server_id IS NOT NULL) OR
    (kind = 'rag_folder_index' AND rag_folder_id IS NOT NULL)
);
CREATE OR REPLACE FUNCTION guard_job_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'job history cannot be deleted' USING ERRCODE = '23514'; END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN RAISE EXCEPTION 'terminal jobs are immutable' USING ERRCODE = '23514'; END IF;
    IF ROW(NEW.project_id, NEW.kind, NEW.repository_id, NEW.document_set_id, NEW.rag_collection_id, NEW.jira_connection_id, NEW.server_id, NEW.rag_folder_id)
        IS DISTINCT FROM ROW(OLD.project_id, OLD.kind, OLD.repository_id, OLD.document_set_id, OLD.rag_collection_id, OLD.jira_connection_id, OLD.server_id, OLD.rag_folder_id) THEN
        RAISE EXCEPTION 'job targets are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TABLE documentation_generations (
    job_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    document_set_id uuid NOT NULL,
    mode text NOT NULL CHECK (mode IN ('build', 'update')),
    plugin_name nonblank NOT NULL DEFAULT 'app-docs',
    plugin_version nonblank NOT NULL,
    plugin_digest sha256 NOT NULL,
    sdk_session_id text,
    base_revision bigint NOT NULL CHECK (base_revision >= 0),
    published_revision bigint CHECK (published_revision > base_revision),
    FOREIGN KEY (job_id, project_id, document_set_id) REFERENCES jobs(id, project_id, document_set_id),
    UNIQUE (job_id, project_id, document_set_id),
    UNIQUE (job_id, project_id)
);

CREATE TABLE documentation_generation_sources (
    job_id uuid NOT NULL,
    project_id uuid NOT NULL,
    repository_id uuid NOT NULL,
    commit_sha git_commit NOT NULL,
    PRIMARY KEY (job_id, repository_id),
    FOREIGN KEY (job_id, project_id) REFERENCES documentation_generations(job_id, project_id),
    FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id)
);

CREATE TABLE document_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    document_set_id uuid NOT NULL,
    path relative_path NOT NULL,
    current_revision_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    FOREIGN KEY (document_set_id, project_id) REFERENCES document_sets(id, project_id),
    UNIQUE (id, project_id, document_set_id),
    UNIQUE (document_set_id, path)
);

-- Bytes live in blobs/<project-id>/<revision-id>, and are immutable.
CREATE TABLE document_revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id uuid NOT NULL,
    project_id uuid NOT NULL,
    document_set_id uuid NOT NULL,
    content_hash sha256 NOT NULL,
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    media_type nonblank NOT NULL DEFAULT 'text/markdown',
    title text,
    front_matter jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(front_matter) = 'object'),
    origin text NOT NULL CHECK (origin IN ('generated', 'uploaded', 'imported')),
    generation_job_id uuid,
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((origin = 'generated') = (generation_job_id IS NOT NULL)),
    FOREIGN KEY (file_id, project_id, document_set_id) REFERENCES document_files(id, project_id, document_set_id),
    FOREIGN KEY (generation_job_id, project_id, document_set_id)
        REFERENCES documentation_generations(job_id, project_id, document_set_id),
    UNIQUE (id, file_id, project_id, document_set_id)
);
CREATE INDEX document_revisions_file ON document_revisions(file_id, created_at DESC);
CREATE INDEX document_revisions_generation ON document_revisions(generation_job_id);
ALTER TABLE document_files ADD CONSTRAINT document_files_current_revision
    FOREIGN KEY (current_revision_id, id, project_id, document_set_id)
    REFERENCES document_revisions(id, file_id, project_id, document_set_id);

CREATE TABLE rag_generations (
    job_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    document_set_id uuid,
    source_revision bigint CHECK (source_revision >= 0),
    source_commit git_commit,
    manifest_hash sha256 NOT NULL,
    embedding_provider nonblank NOT NULL,
    embedding_model nonblank NOT NULL,
    embedding_dimensions integer NOT NULL CHECK (embedding_dimensions > 0),
    index_format nonblank NOT NULL,
    chunker_version nonblank NOT NULL,
    document_count bigint CHECK (document_count >= 0),
    chunk_count bigint CHECK (chunk_count >= 0),
    embedded_chunk_count bigint CHECK (embedded_chunk_count >= 0),
    allow_empty boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (embedded_chunk_count IS NULL OR chunk_count IS NULL OR embedded_chunk_count <= chunk_count),
    CHECK ((document_set_id IS NOT NULL AND source_revision IS NOT NULL AND source_commit IS NULL)
        OR (document_set_id IS NULL AND source_revision IS NULL AND source_commit IS NOT NULL)),
    FOREIGN KEY (job_id, project_id, collection_id) REFERENCES jobs(id, project_id, rag_collection_id),
    FOREIGN KEY (collection_id, project_id, document_set_id)
        REFERENCES rag_collections(id, project_id, document_set_id),
    UNIQUE (job_id, project_id, collection_id),
    UNIQUE (job_id, project_id, document_set_id)
);

-- Exact immutable input, including original paths. A later rename does not
-- change historical citations, and deleted files are omitted on the next run.
CREATE TABLE rag_generation_documents (
    generation_id uuid NOT NULL,
    project_id uuid NOT NULL,
    document_set_id uuid NOT NULL,
    file_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    path relative_path NOT NULL,
    PRIMARY KEY (generation_id, file_id),
    UNIQUE (generation_id, path),
    FOREIGN KEY (generation_id, project_id, document_set_id)
        REFERENCES rag_generations(job_id, project_id, document_set_id),
    FOREIGN KEY (revision_id, file_id, project_id, document_set_id)
        REFERENCES document_revisions(id, file_id, project_id, document_set_id)
);
CREATE INDEX rag_generation_documents_revision ON rag_generation_documents(revision_id);
ALTER TABLE rag_collections ADD CONSTRAINT rag_collections_active_generation
    FOREIGN KEY (active_generation_id, project_id, id)
    REFERENCES rag_generations(job_id, project_id, collection_id);

CREATE TRIGGER touch_updated_at BEFORE UPDATE ON document_sets
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON document_revisions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE FUNCTION guard_rag_activation() RETURNS trigger LANGUAGE plpgsql
SET search_path = ka, pg_temp AS $$
DECLARE generation rag_generations; job jobs;
BEGIN
    IF ROW(NEW.id, NEW.project_id, NEW.kind, NEW.document_set_id, NEW.repository_id)
        IS DISTINCT FROM ROW(OLD.id, OLD.project_id, OLD.kind, OLD.document_set_id, OLD.repository_id) THEN
        RAISE EXCEPTION 'collection sources are immutable; create a new collection' USING ERRCODE = '23514';
    END IF;
    IF NEW.active_generation_id IS NOT DISTINCT FROM OLD.active_generation_id THEN RETURN NEW; END IF;
    IF NEW.active_generation_id IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO generation FROM rag_generations WHERE job_id = NEW.active_generation_id;
    SELECT * INTO job FROM jobs WHERE id = NEW.active_generation_id;
    IF NOT FOUND OR job.status <> 'succeeded'
        OR generation.collection_id IS DISTINCT FROM NEW.id
        OR generation.project_id IS DISTINCT FROM NEW.project_id
        OR generation.document_set_id IS DISTINCT FROM NEW.document_set_id
        OR generation.document_count IS NULL OR generation.chunk_count IS NULL
        OR generation.embedded_chunk_count IS DISTINCT FROM generation.chunk_count
        OR (generation.document_count = 0 AND NOT generation.allow_empty)
        OR (generation.document_count = 0 AND generation.chunk_count <> 0)
        OR (generation.document_count > 0 AND generation.chunk_count = 0) THEN
        RAISE EXCEPTION 'only complete, successfully embedded generations can be activated'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.document_set_id IS NOT NULL AND generation.document_count <> (
        SELECT count(*) FROM rag_generation_documents WHERE generation_id = generation.job_id
    ) THEN
        RAISE EXCEPTION 'generation manifest count does not match documents' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_rag_activation BEFORE UPDATE ON rag_collections
    FOR EACH ROW EXECUTE FUNCTION guard_rag_activation();

-- Locking the parent serializes finalization against last-minute manifest edits.
CREATE FUNCTION guard_rag_snapshot() RETURNS trigger LANGUAGE plpgsql
SET search_path = ka, pg_temp AS $$
DECLARE parent_id uuid; parent_status text; collection_doc_set uuid;
BEGIN
    IF TG_TABLE_NAME = 'rag_generations' THEN
        IF TG_OP = 'INSERT' THEN parent_id := NEW.job_id; ELSE parent_id := OLD.job_id; END IF;
    ELSE
        IF TG_OP = 'INSERT' THEN parent_id := NEW.generation_id; ELSE parent_id := OLD.generation_id; END IF;
    END IF;
    SELECT status INTO parent_status FROM jobs WHERE id = parent_id FOR UPDATE;
    IF parent_status IN ('succeeded', 'failed', 'cancelled') THEN
        RAISE EXCEPTION 'completed RAG snapshots are immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF TG_TABLE_NAME = 'rag_generations' THEN
            IF ROW(OLD.job_id, OLD.project_id, OLD.collection_id, OLD.document_set_id)
                IS DISTINCT FROM ROW(NEW.job_id, NEW.project_id, NEW.collection_id, NEW.document_set_id) THEN
                RAISE EXCEPTION 'generation target is immutable' USING ERRCODE = '23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'replace snapshot rows instead of updating them' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF TG_TABLE_NAME = 'rag_generations' AND TG_OP <> 'DELETE' THEN
        SELECT document_set_id INTO collection_doc_set FROM rag_collections WHERE id = NEW.collection_id;
        IF collection_doc_set IS DISTINCT FROM NEW.document_set_id THEN
            RAISE EXCEPTION 'generation source must match collection source' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_rag_snapshot BEFORE INSERT OR UPDATE OR DELETE ON rag_generations
    FOR EACH ROW EXECUTE FUNCTION guard_rag_snapshot();
CREATE TRIGGER guard_rag_snapshot BEFORE INSERT OR UPDATE OR DELETE ON rag_generation_documents
    FOR EACH ROW EXECUTE FUNCTION guard_rag_snapshot();

CREATE FUNCTION guard_documentation_snapshot() RETURNS trigger LANGUAGE plpgsql
SET search_path = ka, pg_temp AS $$
DECLARE parent_id uuid; parent_status text;
BEGIN
    IF TG_OP = 'INSERT' THEN parent_id := NEW.job_id; ELSE parent_id := OLD.job_id; END IF;
    SELECT status INTO parent_status FROM jobs WHERE id = parent_id FOR UPDATE;
    IF parent_status IN ('succeeded', 'failed', 'cancelled') THEN
        RAISE EXCEPTION 'completed documentation provenance is immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND ROW(OLD.job_id, OLD.project_id) IS DISTINCT FROM ROW(NEW.job_id, NEW.project_id) THEN
        RAISE EXCEPTION 'generation identity is immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_documentation_snapshot BEFORE INSERT OR UPDATE OR DELETE ON documentation_generations
    FOR EACH ROW EXECUTE FUNCTION guard_documentation_snapshot();
CREATE TRIGGER guard_documentation_snapshot BEFORE INSERT OR UPDATE OR DELETE ON documentation_generation_sources
    FOR EACH ROW EXECUTE FUNCTION guard_documentation_snapshot();

-- The worker calls this after closing/checkpointing and durably renaming the
-- finished index directory. Both DB changes roll back if activation fails.
CREATE FUNCTION publish_rag_generation(target_job_id uuid, expected_lease_token uuid)
RETURNS void LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE job jobs;
BEGIN
    SELECT * INTO job FROM jobs WHERE id = target_job_id FOR UPDATE;
    IF NOT FOUND OR job.kind <> 'rag_index' OR job.status <> 'running'
        OR expected_lease_token IS NULL OR job.lease_token IS DISTINCT FROM expected_lease_token
        OR job.lease_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'index job lease is not current' USING ERRCODE = '23514';
    END IF;
    PERFORM 1 FROM rag_collections WHERE id = job.rag_collection_id FOR UPDATE;
    UPDATE jobs SET status = 'succeeded', finished_at = clock_timestamp(),
        worker_id = NULL, lease_token = NULL, lease_expires_at = NULL WHERE id = job.id;
    UPDATE rag_collections SET active_generation_id = job.id WHERE id = job.rag_collection_id;
END;
$$;

COMMIT;
