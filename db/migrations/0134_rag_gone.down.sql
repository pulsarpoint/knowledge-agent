BEGIN;
SET search_path = ka, public;

-- Back to 0133: the RAG tables, the jobs columns and checks, and the functions as 0133 had them, empty. Removed jobs and
-- collection access entries do not come back.
CREATE TABLE rag_workspaces (
    project_id uuid NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    settings jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    embedding_model_id uuid,
    CONSTRAINT rag_workspaces_pkey PRIMARY KEY (project_id),
    CONSTRAINT rag_workspaces_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id),
    CONSTRAINT rag_workspaces_embedding_model_id_fkey FOREIGN KEY (embedding_model_id) REFERENCES llm_models(id),
    CONSTRAINT rag_workspaces_revision_check CHECK (revision > 0),
    CONSTRAINT rag_workspaces_settings_check CHECK (jsonb_typeof(settings) = 'object')
);
CREATE UNIQUE INDEX rag_workspaces_singleton ON rag_workspaces ((true));
COMMENT ON TABLE rag_workspaces IS 'File processing settings and a shared embedding model selection. Source root is deployment-only.';

CREATE TABLE rag_folders (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    path text NOT NULL,
    description text NOT NULL DEFAULT '',
    deleted_at timestamptz,
    source_cleanup_pending boolean NOT NULL DEFAULT false,
    CONSTRAINT rag_folders_pkey PRIMARY KEY (id),
    CONSTRAINT rag_folders_id_project_id_key UNIQUE (id, project_id),
    CONSTRAINT rag_folders_project_id_fkey FOREIGN KEY (project_id) REFERENCES rag_workspaces(project_id),
    CONSTRAINT rag_folders_path_check CHECK (path ~ '^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*$'),
    CONSTRAINT rag_folders_description_check CHECK (length(description) <= 10000),
    CONSTRAINT rag_folder_cleanup_requires_deletion CHECK (NOT source_cleanup_pending OR deleted_at IS NOT NULL)
);
CREATE UNIQUE INDEX rag_folders_active_path ON rag_folders(project_id, path) WHERE deleted_at IS NULL;
CREATE INDEX rag_folder_pending_cleanup ON rag_folders(project_id) WHERE source_cleanup_pending;
COMMENT ON COLUMN rag_folders.deleted_at IS 'Deleted folders retain only identity/path for immutable job history; descriptions, files, chunks, and unreferenced vectors are purged.';
COMMENT ON COLUMN rag_folders.source_cleanup_pending IS 'Durable recursive source removal after index deletion commits; the worker retries interrupted or failed filesystem cleanup.';

CREATE TABLE rag_vectors (
    project_id uuid NOT NULL,
    profile text NOT NULL,
    input_hash text NOT NULL,
    embedding vector NOT NULL,
    CONSTRAINT rag_vectors_pkey PRIMARY KEY (project_id, profile, input_hash),
    CONSTRAINT rag_vectors_project_id_fkey FOREIGN KEY (project_id) REFERENCES rag_workspaces(project_id),
    CONSTRAINT rag_vectors_input_hash_check CHECK (length(input_hash) = 64)
);

CREATE TABLE rag_collections (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    search_mode text NOT NULL DEFAULT 'full_text',
    embeddings_enabled boolean NOT NULL DEFAULT false,
    include text[] NOT NULL DEFAULT '{**/*.md,**/*.markdown,**/*.txt}',
    exclude text[] NOT NULL DEFAULT '{}',
    chunk_size integer NOT NULL DEFAULT 2000,
    chunk_overlap integer NOT NULL DEFAULT 200,
    revision bigint NOT NULL DEFAULT 1,
    synced_revision bigint,
    last_synced_at timestamptz,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    auto_sync boolean NOT NULL DEFAULT false,
    poll_interval_minutes integer NOT NULL DEFAULT 5,
    last_polled_at timestamptz,
    CONSTRAINT rag_collections_pkey PRIMARY KEY (id),
    CONSTRAINT rag_collections_id_project_id_key UNIQUE (id, project_id),
    CONSTRAINT rag_collections_project_id_fkey FOREIGN KEY (project_id) REFERENCES rag_workspaces(project_id),
    CONSTRAINT rag_collections_name_check CHECK (name ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(name) <= 60),
    CONSTRAINT rag_collections_description_check CHECK (length(description) <= 2000),
    CONSTRAINT rag_collections_search_mode_check CHECK (search_mode IN ('full_text', 'embedding', 'hybrid')),
    CONSTRAINT rag_collections_include_check CHECK (cardinality(include) BETWEEN 1 AND 50),
    CONSTRAINT rag_collections_exclude_check CHECK (cardinality(exclude) <= 50),
    CONSTRAINT rag_collections_chunk_size_check CHECK (chunk_size BETWEEN 200 AND 12000),
    CONSTRAINT rag_collections_chunk_overlap_check CHECK (chunk_overlap BETWEEN 0 AND 2000),
    CONSTRAINT rag_collections_check CHECK (chunk_overlap < chunk_size),
    CONSTRAINT rag_collections_revision_check CHECK (revision > 0),
    CONSTRAINT rag_collections_poll_interval_minutes_check CHECK (poll_interval_minutes BETWEEN 1 AND 1440)
);
CREATE UNIQUE INDEX rag_collections_active_name ON rag_collections(project_id, name) WHERE deleted_at IS NULL;
COMMENT ON TABLE rag_collections IS 'Searchable unit with its own settings; its folders are listed in rag_collection_paths.';

CREATE TABLE rag_collection_paths (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    collection_id uuid NOT NULL,
    source_path text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rag_collection_paths_pkey PRIMARY KEY (id),
    CONSTRAINT rag_collection_paths_id_collection_id_key UNIQUE (id, collection_id),
    CONSTRAINT rag_collection_paths_collection_id_source_path_key UNIQUE (collection_id, source_path),
    CONSTRAINT rag_collection_paths_collection_id_fkey FOREIGN KEY (collection_id)
        REFERENCES rag_collections(id) ON DELETE CASCADE,
    CONSTRAINT rag_collection_paths_source_path_check CHECK (
        length(source_path) <= 500 AND source_path ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$')
);
COMMENT ON TABLE rag_collection_paths IS 'A folder under DATA_ROOT connected to a collection in RAG; sources only write files into it.';

CREATE TABLE rag_files (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    path text NOT NULL,
    content_hash text NOT NULL,
    processing_hash text NOT NULL,
    embedding_profile text NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now(),
    job_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    path_id uuid NOT NULL,
    CONSTRAINT rag_files_pkey PRIMARY KEY (id),
    CONSTRAINT rag_files_id_project_id_key UNIQUE (id, project_id),
    CONSTRAINT rag_files_collection_path UNIQUE (collection_id, path),
    CONSTRAINT rag_files_job_id_project_id_fkey FOREIGN KEY (job_id, project_id) REFERENCES jobs(id, project_id),
    CONSTRAINT rag_files_collection_fk FOREIGN KEY (collection_id, project_id)
        REFERENCES rag_collections(id, project_id),
    CONSTRAINT rag_files_path_fk FOREIGN KEY (path_id, collection_id)
        REFERENCES rag_collection_paths(id, collection_id) ON DELETE CASCADE,
    CONSTRAINT rag_files_content_hash_check CHECK (length(content_hash) = 64),
    CONSTRAINT rag_files_processing_hash_check CHECK (length(processing_hash) = 64)
);
CREATE INDEX rag_files_path ON rag_files(path_id);

CREATE TABLE rag_file_log (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    collection_id uuid NOT NULL,
    path_id uuid NOT NULL,
    path text NOT NULL,
    size bigint,
    mtime double precision,
    content_hash text,
    fingerprint text,
    status text NOT NULL,
    error text,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    indexed_at timestamptz,
    removed_at timestamptz,
    CONSTRAINT rag_file_log_pkey PRIMARY KEY (id),
    CONSTRAINT rag_file_log_collection_id_path_key UNIQUE (collection_id, path),
    CONSTRAINT rag_file_log_path_id_collection_id_fkey FOREIGN KEY (path_id, collection_id)
        REFERENCES rag_collection_paths(id, collection_id) ON DELETE CASCADE,
    CONSTRAINT rag_file_log_size_check CHECK (size >= 0),
    CONSTRAINT rag_file_log_content_hash_check CHECK (length(content_hash) = 64),
    CONSTRAINT rag_file_log_fingerprint_check CHECK (length(fingerprint) = 64),
    CONSTRAINT rag_file_log_error_check CHECK (length(error) <= 2000),
    CONSTRAINT rag_file_log_status_check CHECK (status IN (
        'indexed', 'skipped_binary', 'skipped_too_large', 'skipped_unreadable', 'failed', 'removed'))
);
CREATE INDEX rag_file_log_path ON rag_file_log(path_id);
CREATE INDEX rag_file_log_status ON rag_file_log(collection_id, status, path);
COMMENT ON TABLE rag_file_log IS 'Per-file sync history of a collection: size, mtime, content hash, processing fingerprint and status.';

CREATE TABLE rag_chunks (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    file_id uuid NOT NULL,
    position integer NOT NULL,
    text text NOT NULL,
    input_hash text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    CONSTRAINT rag_chunks_pkey PRIMARY KEY (id),
    CONSTRAINT rag_chunks_file_id_position_key UNIQUE (file_id, position),
    CONSTRAINT rag_chunks_file_id_project_id_fkey FOREIGN KEY (file_id, project_id)
        REFERENCES rag_files(id, project_id) ON DELETE CASCADE,
    CONSTRAINT rag_chunks_position_check CHECK (position >= 0),
    CONSTRAINT rag_chunks_input_hash_check CHECK (length(input_hash) = 64)
);
CREATE INDEX rag_chunks_search ON rag_chunks USING gin(search_vector);
CREATE INDEX rag_chunks_project ON rag_chunks(project_id);

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check, DROP CONSTRAINT jobs_check1;
ALTER TABLE jobs ADD COLUMN rag_folder_id uuid, ADD COLUMN rag_collection_id uuid;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN (
    'repository_sync', 'code_index', 'jira_sync', 'server_test', 'rag_folder_index', 'rag_collection_sync'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check CHECK (
    num_nonnulls(repository_id, jira_connection_id, server_id, rag_folder_id, rag_collection_id) = 1);
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    (kind IN ('repository_sync', 'code_index') AND repository_id IS NOT NULL) OR
    (kind = 'jira_sync' AND jira_connection_id IS NOT NULL) OR
    (kind = 'server_test' AND server_id IS NOT NULL) OR
    (kind = 'rag_folder_index' AND rag_folder_id IS NOT NULL) OR
    (kind = 'rag_collection_sync' AND rag_collection_id IS NOT NULL)
);
ALTER TABLE jobs ADD CONSTRAINT jobs_rag_folder_fk FOREIGN KEY (rag_folder_id, project_id)
    REFERENCES rag_folders(id, project_id);
ALTER TABLE jobs ADD CONSTRAINT jobs_rag_collection_fk FOREIGN KEY (rag_collection_id, project_id)
    REFERENCES rag_collections(id, project_id);
CREATE UNIQUE INDEX rag_collection_one_active_job ON jobs(rag_collection_id)
    WHERE kind = 'rag_collection_sync' AND status IN ('queued', 'running', 'waiting_input');
CREATE OR REPLACE FUNCTION guard_job_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'job history cannot be deleted' USING ERRCODE = '23514'; END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN RAISE EXCEPTION 'terminal jobs are immutable' USING ERRCODE = '23514'; END IF;
    IF ROW(NEW.project_id, NEW.kind, NEW.repository_id, NEW.jira_connection_id, NEW.server_id, NEW.rag_folder_id, NEW.rag_collection_id)
        IS DISTINCT FROM ROW(OLD.project_id, OLD.kind, OLD.repository_id, OLD.jira_connection_id, OLD.server_id, OLD.rag_folder_id, OLD.rag_collection_id) THEN
        RAISE EXCEPTION 'job targets are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION embedding_model_updated() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.api_format='embeddings')<>(NEW.api_format='embeddings') THEN
        RAISE EXCEPTION 'Create a separate connection when changing between chat and embeddings';
    END IF;
    UPDATE ka.rag_workspaces SET revision=revision+1,updated_at=now() WHERE embedding_model_id=NEW.id;
    RETURN NEW;
END $$;

COMMIT;
