CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
SET search_path = ka, public;

CREATE TABLE rag_workspaces (
    project_id uuid PRIMARY KEY REFERENCES projects(id),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    settings jsonb NOT NULL CHECK (jsonb_typeof(settings) = 'object'),
    embedding_secret bytea,
    chat_secret bytea,
    updated_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE rag_workspaces IS 'Runtime processing/model settings. Secrets are AES-256-GCM nonce + ciphertext + tag; AAD binds project and purpose. Source root is deployment-only.';

CREATE TABLE rag_folders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES rag_workspaces(project_id),
    path text NOT NULL CHECK (path ~ '^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*$'),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 10000),
    UNIQUE (project_id, path),
    UNIQUE (id, project_id)
);

ALTER TABLE jobs ADD COLUMN rag_folder_id uuid;
ALTER TABLE jobs ADD CONSTRAINT jobs_rag_folder_fk FOREIGN KEY (rag_folder_id, project_id) REFERENCES rag_folders(id, project_id);
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check, DROP CONSTRAINT jobs_check1;
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
CREATE UNIQUE INDEX rag_one_active_job ON jobs(project_id) WHERE kind = 'rag_folder_index' AND status IN ('queued', 'running', 'waiting_input');
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

CREATE TABLE rag_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    folder_id uuid NOT NULL,
    path text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('file', 'folder')),
    content_hash text NOT NULL CHECK (length(content_hash) = 64),
    processing_hash text NOT NULL CHECK (length(processing_hash) = 64),
    embedding_profile text NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now(),
    job_id uuid NOT NULL,
    FOREIGN KEY (folder_id, project_id) REFERENCES rag_folders(id, project_id),
    FOREIGN KEY (job_id, project_id) REFERENCES jobs(id, project_id),
    UNIQUE (project_id, path, kind),
    UNIQUE (id, project_id)
);
CREATE TABLE rag_vectors (
    project_id uuid NOT NULL REFERENCES rag_workspaces(project_id),
    profile text NOT NULL,
    input_hash text NOT NULL CHECK (length(input_hash) = 64),
    embedding public.vector NOT NULL,
    PRIMARY KEY (project_id, profile, input_hash)
);
CREATE TABLE rag_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    file_id uuid NOT NULL,
    position integer NOT NULL CHECK (position >= 0),
    text text NOT NULL,
    input_hash text NOT NULL CHECK (length(input_hash) = 64),
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    FOREIGN KEY (file_id, project_id) REFERENCES rag_files(id, project_id) ON DELETE CASCADE,
    UNIQUE(file_id, position)
);
CREATE INDEX rag_files_folder ON rag_files(folder_id);
CREATE INDEX rag_chunks_search ON rag_chunks USING gin(search_vector);
CREATE INDEX rag_chunks_project ON rag_chunks(project_id);
