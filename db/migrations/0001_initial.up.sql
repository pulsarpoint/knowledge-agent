-- Application metadata only. Local RAG keeps v2's SQLite/FTS5/sqlite-vec format.
-- Applied transactionally by db/migrate.sh; never run against a v2 database.
CREATE SCHEMA ka;
SET search_path = ka, public;

CREATE DOMAIN nonblank AS text CHECK (length(btrim(VALUE)) > 0);
CREATE DOMAIN sha256 AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');
CREATE DOMAIN git_commit AS text CHECK (VALUE ~ '^([0-9a-f]{40}|[0-9a-f]{64})$');
CREATE DOMAIN relative_path AS text CHECK (
    length(VALUE) BETWEEN 1 AND 1024
    AND VALUE !~ '(^/|/$|//|(^|/)\.{1,2}(/|$)|[[:cntrl:]:])'
    AND position(chr(92) IN VALUE) = 0
);
CREATE DOMAIN otel_trace_id AS text CHECK (VALUE ~ '^[0-9a-f]{32}$');

CREATE TABLE principals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issuer nonblank NOT NULL,
    subject nonblank NOT NULL,
    email text,
    display_name text,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

CREATE TABLE projects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    name nonblank NOT NULL,
    description text NOT NULL DEFAULT '',
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    archived_at timestamptz
);

CREATE TABLE project_members (
    project_id uuid NOT NULL REFERENCES projects(id),
    principal_id uuid NOT NULL REFERENCES principals(id),
    role text NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, principal_id)
);
CREATE INDEX project_members_principal ON project_members(principal_id);

-- Envelope-encrypted by the API; encryption key lives outside PostgreSQL.
CREATE TABLE credentials (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name nonblank NOT NULL,
    kind text NOT NULL CHECK (kind IN (
        'git_https_token', 'ssh_private_key', 'jira_api_token', 'jira_pat'
    )),
    ciphertext bytea NOT NULL CHECK (octet_length(ciphertext) > 0),
    key_version nonblank NOT NULL,
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name),
    UNIQUE (id, project_id, kind)
);

CREATE TABLE repositories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    slug text NOT NULL CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    name nonblank NOT NULL,
    remote_url nonblank NOT NULL CHECK (
        remote_url ~ '^(https://[^/@[:space:]]+/|ssh://[^[:space:]]+/|git@[^:[:space:]]+:)'
    ),
    requested_ref nonblank, -- NULL means discover the remote default branch.
    credential_id uuid,
    credential_kind text CHECK (credential_kind IN ('git_https_token', 'ssh_private_key')),
    current_commit git_commit,
    synced_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    archived_at timestamptz,
    CHECK ((credential_id IS NULL) = (credential_kind IS NULL)),
    FOREIGN KEY (credential_id, project_id, credential_kind)
        REFERENCES credentials(id, project_id, kind),
    UNIQUE (id, project_id),
    UNIQUE (project_id, slug)
);

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

CREATE TABLE jira_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name nonblank NOT NULL,
    deployment text NOT NULL DEFAULT 'data_center' CHECK (deployment IN ('cloud', 'data_center')),
    base_url nonblank NOT NULL CHECK (base_url ~ '^https?://[^/@[:space:]]+'),
    auth_kind text NOT NULL DEFAULT 'jira_pat' CHECK (auth_kind IN ('jira_api_token', 'jira_pat')),
    username nonblank,
    credential_id uuid,
    jql nonblank NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    last_tested_at timestamptz,
    last_test_error text,
    last_successful_sync_at timestamptz,
    sync_watermark timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((deployment = 'cloud' AND auth_kind = 'jira_api_token' AND username IS NOT NULL)
        OR (deployment = 'data_center' AND auth_kind = 'jira_pat')),
    FOREIGN KEY (credential_id, project_id, auth_kind)
        REFERENCES credentials(id, project_id, kind),
    UNIQUE (id, project_id),
    UNIQUE (project_id, name)
);

CREATE TABLE servers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name nonblank NOT NULL,
    hostname nonblank NOT NULL CHECK (hostname !~ '[[:space:]/@]'),
    port integer NOT NULL DEFAULT 22 CHECK (port BETWEEN 1 AND 65535),
    username nonblank NOT NULL,
    auth_kind text NOT NULL DEFAULT 'ssh_private_key'
        CHECK (auth_kind IN ('ssh_private_key', 'ssh_agent')),
    credential_id uuid,
    credential_kind text NOT NULL DEFAULT 'ssh_private_key' CHECK (credential_kind = 'ssh_private_key'),
    known_hosts text,
    allow_sudo boolean NOT NULL DEFAULT false,
    enabled boolean NOT NULL DEFAULT true,
    last_tested_at timestamptz,
    last_test_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (auth_kind <> 'ssh_agent' OR credential_id IS NULL),
    FOREIGN KEY (credential_id, project_id, credential_kind)
        REFERENCES credentials(id, project_id, kind),
    UNIQUE (id, project_id),
    UNIQUE (project_id, name)
);

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

CREATE TABLE jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    kind text NOT NULL CHECK (kind IN (
        'repository_sync', 'documentation_generate', 'rag_index', 'jira_sync', 'server_test'
    )),
    repository_id uuid,
    document_set_id uuid,
    rag_collection_id uuid,
    jira_connection_id uuid,
    server_id uuid,
    status text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'waiting_input', 'succeeded', 'failed', 'cancelled')
    ),
    requested_by uuid REFERENCES principals(id),
    idempotency_key nonblank,
    input jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(input) = 'object'),
    result jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(result) = 'object'),
    error text,
    trace_id otel_trace_id,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    worker_id nonblank,
    lease_token uuid,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    CHECK (num_nonnulls(repository_id, document_set_id, rag_collection_id, jira_connection_id, server_id) = 1),
    CHECK ((kind = 'repository_sync' AND repository_id IS NOT NULL)
        OR (kind = 'documentation_generate' AND document_set_id IS NOT NULL)
        OR (kind = 'rag_index' AND rag_collection_id IS NOT NULL)
        OR (kind = 'jira_sync' AND jira_connection_id IS NOT NULL)
        OR (kind = 'server_test' AND server_id IS NOT NULL)),
    CHECK ((status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)),
    CHECK (status NOT IN ('running', 'waiting_input', 'succeeded') OR started_at IS NOT NULL),
    CHECK (finished_at IS NULL OR finished_at >= created_at),
    CHECK (started_at IS NULL OR started_at >= created_at),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
    CHECK ((status = 'running' AND worker_id IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (status <> 'running' AND worker_id IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)),
    FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id),
    FOREIGN KEY (document_set_id, project_id) REFERENCES document_sets(id, project_id),
    FOREIGN KEY (rag_collection_id, project_id) REFERENCES rag_collections(id, project_id),
    FOREIGN KEY (jira_connection_id, project_id) REFERENCES jira_connections(id, project_id),
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id),
    UNIQUE (id, project_id),
    UNIQUE (id, project_id, document_set_id),
    UNIQUE (id, project_id, rag_collection_id),
    UNIQUE (project_id, idempotency_key)
);
CREATE INDEX jobs_queue ON jobs(created_at, id) WHERE status = 'queued';
CREATE INDEX jobs_expired_leases ON jobs(lease_expires_at) WHERE status = 'running';
CREATE INDEX jobs_project_history ON jobs(project_id, created_at DESC, id);
CREATE UNIQUE INDEX jobs_one_repository_sync ON jobs(repository_id)
    WHERE status IN ('queued', 'running', 'waiting_input');
CREATE UNIQUE INDEX jobs_one_document_generation ON jobs(document_set_id)
    WHERE status IN ('queued', 'running', 'waiting_input');
CREATE UNIQUE INDEX jobs_one_rag_build ON jobs(rag_collection_id)
    WHERE status IN ('queued', 'running', 'waiting_input');
CREATE UNIQUE INDEX jobs_one_jira_sync ON jobs(jira_connection_id)
    WHERE status IN ('queued', 'running', 'waiting_input');
CREATE UNIQUE INDEX jobs_one_server_test ON jobs(server_id)
    WHERE status IN ('queued', 'running', 'waiting_input');

CREATE TABLE job_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs(id),
    event_type nonblank NOT NULL,
    message text NOT NULL,
    data jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(data) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX job_events_stream ON job_events(job_id, id);

CREATE TABLE job_questions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES jobs(id),
    question nonblank NOT NULL,
    suggested_answer text,
    answer nonblank,
    answered_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    answered_at timestamptz,
    CHECK ((answer IS NULL AND answered_by IS NULL AND answered_at IS NULL)
        OR (answer IS NOT NULL AND answered_by IS NOT NULL AND answered_at IS NOT NULL))
);
CREATE INDEX job_questions_job ON job_questions(job_id);

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

CREATE TABLE jira_tickets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    remote_id nonblank NOT NULL,
    issue_key nonblank NOT NULL,
    jira_project_key nonblank NOT NULL,
    summary nonblank NOT NULL,
    description jsonb, -- Cloud ADF object or Data Center JSON string.
    issue_type text,
    status_id text,
    status_name text,
    priority text,
    assignee_id text,
    assignee_name text,
    reporter_id text,
    labels text[] NOT NULL DEFAULT '{}',
    raw_payload jsonb NOT NULL CHECK (jsonb_typeof(raw_payload) = 'object'),
    source_created_at timestamptz,
    source_updated_at timestamptz NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    out_of_scope_at timestamptz,
    FOREIGN KEY (connection_id, project_id) REFERENCES jira_connections(id, project_id),
    UNIQUE (connection_id, remote_id),
    UNIQUE (connection_id, issue_key)
);
CREATE INDEX jira_tickets_list ON jira_tickets(project_id, source_updated_at DESC, id);
CREATE INDEX jira_tickets_status ON jira_tickets(connection_id, status_id);
CREATE INDEX jira_tickets_search ON jira_tickets USING gin (
    to_tsvector('simple', issue_key || ' ' || summary)
);

CREATE TABLE chat_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    owner_id uuid REFERENCES principals(id),
    title text,
    share_token_hash sha256 UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    UNIQUE (id, project_id)
);
CREATE INDEX chat_sessions_owner ON chat_sessions(project_id, owner_id, updated_at DESC);

CREATE TABLE chat_items (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES chat_sessions(id),
    position bigint NOT NULL CHECK (position >= 0),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, position)
);

-- Deliberate snapshots without a session FK: deleting chat history must not
-- erase the admin audit transcript, matching v2 behavior.
CREATE TABLE chat_turns (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    session_ref nonblank NOT NULL,
    actor_id uuid REFERENCES principals(id),
    actor_email text,
    question text NOT NULL,
    answer text NOT NULL,
    sources_used jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(sources_used) = 'array'),
    not_covered boolean NOT NULL DEFAULT false,
    escalated boolean NOT NULL DEFAULT false,
    trace_id otel_trace_id,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, project_id)
);
CREATE INDEX chat_turns_session ON chat_turns(project_id, session_ref, created_at, id);
CREATE INDEX chat_turns_actor ON chat_turns(project_id, actor_id, created_at DESC);

CREATE TABLE documentation_gaps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    chat_turn_id uuid,
    question text NOT NULL,
    near_misses jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(near_misses) = 'array'),
    code_answer text,
    code_citations jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(code_citations) = 'array'),
    code_not_found boolean,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'dismissed')),
    resolution_note text,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'open') = (resolved_at IS NULL)),
    FOREIGN KEY (chat_turn_id, project_id) REFERENCES chat_turns(id, project_id)
);
CREATE INDEX documentation_gaps_open ON documentation_gaps(project_id, created_at DESC)
    WHERE status = 'open';

CREATE TABLE audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id uuid REFERENCES projects(id),
    actor_id uuid REFERENCES principals(id),
    action nonblank NOT NULL,
    resource_type nonblank NOT NULL,
    resource_id uuid,
    details jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details) = 'object'),
    trace_id otel_trace_id,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_project ON audit_events(project_id, created_at DESC, id);

CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'projects', 'credentials', 'repositories', 'document_sets',
        'jira_connections', 'servers', 'chat_sessions'
    ] LOOP
        EXECUTE format('CREATE TRIGGER touch_updated_at BEFORE UPDATE ON ka.%I '
            'FOR EACH ROW EXECUTE FUNCTION ka.touch_updated_at()', table_name);
    END LOOP;
END;
$$;

CREATE FUNCTION reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% records are immutable', TG_TABLE_NAME USING ERRCODE = '23514';
END;
$$;
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['document_revisions', 'job_events', 'chat_turns', 'audit_events'] LOOP
        EXECUTE format('CREATE TRIGGER reject_mutation BEFORE UPDATE OR DELETE ON ka.%I '
            'FOR EACH ROW EXECUTE FUNCTION ka.reject_mutation()', table_name);
    END LOOP;
END;
$$;

-- A terminal job cannot be revived or repointed. Retry creates a new job.
CREATE FUNCTION guard_job_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'job history cannot be deleted' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN
        RAISE EXCEPTION 'terminal jobs are immutable' USING ERRCODE = '23514';
    END IF;
    IF ROW(NEW.project_id, NEW.kind, NEW.repository_id, NEW.document_set_id,
           NEW.rag_collection_id, NEW.jira_connection_id, NEW.server_id)
        IS DISTINCT FROM ROW(OLD.project_id, OLD.kind, OLD.repository_id, OLD.document_set_id,
           OLD.rag_collection_id, OLD.jira_connection_id, OLD.server_id) THEN
        RAISE EXCEPTION 'job targets are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_job_update BEFORE UPDATE OR DELETE ON jobs
    FOR EACH ROW EXECUTE FUNCTION guard_job_update();

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

COMMENT ON SCHEMA ka IS 'Knowledge Agent v3 application data; Keycloak and Phoenix own separate state.';
COMMENT ON COLUMN document_sets.revision IS 'API increments once per atomic file-batch publication under a row lock.';
COMMENT ON COLUMN servers.allow_sudo IS 'Execution policy ceiling; does not itself authorize any command.';
COMMENT ON COLUMN jobs.lease_token IS 'Worker must compare token and nonexpired lease on every completion or publication.';
COMMENT ON TABLE credentials IS 'AEAD envelope (nonce, ciphertext, tag); bind project/id/kind as associated data. Never return ciphertext via admin API.';
