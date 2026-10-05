SET search_path = ka, public;

CREATE TABLE rag_collections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES rag_workspaces(project_id),
    name text NOT NULL CHECK (name ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(name) <= 60),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 2000),
    source_path text NOT NULL CHECK (length(source_path) <= 500 AND source_path ~
        '^(code|code-index|rag-data|generated-documents|docs)(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$'),
    search_mode text NOT NULL DEFAULT 'full_text' CHECK (search_mode IN ('full_text','embedding','hybrid')),
    embeddings_enabled boolean NOT NULL DEFAULT false,
    include text[] NOT NULL DEFAULT '{**/*.md,**/*.markdown,**/*.txt}' CHECK (cardinality(include) BETWEEN 1 AND 50),
    exclude text[] NOT NULL DEFAULT '{}' CHECK (cardinality(exclude) <= 50),
    chunk_size integer NOT NULL DEFAULT 2000 CHECK (chunk_size BETWEEN 200 AND 12000),
    chunk_overlap integer NOT NULL DEFAULT 200 CHECK (chunk_overlap BETWEEN 0 AND 2000),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    synced_revision bigint,
    last_synced_at timestamptz,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (chunk_overlap < chunk_size),
    UNIQUE (id, project_id)
);
CREATE UNIQUE INDEX rag_collections_active_name ON rag_collections(project_id, name) WHERE deleted_at IS NULL;
COMMENT ON TABLE rag_collections IS 'Searchable unit: one folder under DATA_ROOT indexed in place with its own settings.';

-- One collection per live top-level folder; names are slugs of the folder name.
WITH tops AS (
    SELECT d.project_id, d.path, d.description,
           trim(both '-' from lower(regexp_replace(d.path, '[^A-Za-z0-9]+', '-', 'g'))) AS slug
    FROM rag_folders d WHERE d.deleted_at IS NULL AND position('/' in d.path) = 0
), named AS (
    SELECT *, CASE WHEN count(*) OVER (PARTITION BY project_id, slug) > 1 OR slug = ''
                   THEN left(coalesce(nullif(slug, ''), 'folder'), 50) || '-' || left(md5(path), 6) ELSE slug END AS name
    FROM tops
)
INSERT INTO rag_collections(project_id, name, description, source_path, embeddings_enabled, include,
                            chunk_size, chunk_overlap, last_synced_at, synced_revision)
SELECT n.project_id, n.name, left(n.description, 2000), 'rag-data/' || n.path,
       coalesce((w.settings->>'embedding_enabled')::boolean, false),
       coalesce((SELECT array_agg('**/*' || e ORDER BY e) FROM jsonb_array_elements_text(w.settings->'extensions') e),
                '{**/*.md,**/*.markdown,**/*.txt}'),
       coalesce((w.settings->>'chunk_size')::int, 2000), coalesce((w.settings->>'chunk_overlap')::int, 200),
       synced.at, CASE WHEN synced.at IS NULL THEN NULL ELSE 1 END
FROM named n JOIN rag_workspaces w ON w.project_id = n.project_id
LEFT JOIN LATERAL (
    SELECT max(f.indexed_at) AS at FROM rag_files f JOIN rag_folders s ON s.id = f.folder_id
    WHERE s.project_id = n.project_id AND (s.path = n.path OR starts_with(s.path, n.path || '/')) AND f.kind = 'file'
) synced ON true;

-- The documentation export folder; old v<n>/ version folders are not indexed.
INSERT INTO rag_collections(project_id, name, description, source_path, embeddings_enabled, exclude)
SELECT w.project_id,
       CASE WHEN EXISTS (SELECT 1 FROM rag_collections c WHERE c.project_id = w.project_id AND c.name = 'documents')
            THEN 'generated-documents' ELSE 'documents' END,
       'Documents from Docs → Documents (current version of each document).', 'generated-documents',
       coalesce((w.settings->>'embedding_enabled')::boolean, false), '{**/v[0-9]*/**}'
FROM rag_workspaces w;

-- Indexed files move into their collection with collection-relative paths.
DELETE FROM rag_files WHERE kind = 'folder';
ALTER TABLE rag_files ADD COLUMN collection_id uuid;
UPDATE rag_files f SET collection_id = c.id, path = substr(f.path, length(split_part(f.path, '/', 1)) + 2)
FROM rag_collections c
WHERE c.project_id = f.project_id AND c.deleted_at IS NULL AND c.source_path = 'rag-data/' || split_part(f.path, '/', 1);
DELETE FROM rag_files WHERE collection_id IS NULL;
ALTER TABLE rag_files DROP CONSTRAINT rag_files_project_id_path_kind_key;
ALTER TABLE rag_files DROP COLUMN folder_id, DROP COLUMN kind;
ALTER TABLE rag_files ALTER COLUMN collection_id SET NOT NULL,
    ADD CONSTRAINT rag_files_collection_fk FOREIGN KEY (collection_id, project_id) REFERENCES rag_collections(id, project_id),
    ADD CONSTRAINT rag_files_collection_path UNIQUE (collection_id, path);

-- Settings that moved to collections leave the workspace.
UPDATE rag_workspaces SET settings = jsonb_build_object('max_file_bytes', coalesce((settings->>'max_file_bytes')::int, 2000000)),
    revision = revision + 1, updated_at = now();

-- Jobs: per-collection sync, one active job per collection; folder jobs stay valid history.
UPDATE jobs SET status = 'cancelled', finished_at = now(), error = 'Replaced by RAG collections; refresh the collection',
    worker_id = NULL, lease_token = NULL, lease_expires_at = NULL
WHERE kind = 'rag_folder_index' AND status IN ('queued', 'running', 'waiting_input');
DROP INDEX rag_one_active_job;
ALTER TABLE jobs ADD COLUMN rag_collection_id uuid,
    ADD CONSTRAINT jobs_rag_collection_fk FOREIGN KEY (rag_collection_id, project_id) REFERENCES rag_collections(id, project_id);
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check, DROP CONSTRAINT jobs_check1;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN
    ('repository_sync','code_index','documentation_generate','jira_sync','server_test','rag_folder_index','rag_collection_sync'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check CHECK (
    num_nonnulls(repository_id, jira_connection_id, server_id, rag_folder_id, rag_collection_id) =
    CASE WHEN kind = 'documentation_generate' THEN 0 ELSE 1 END);
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    kind = 'documentation_generate' OR (kind IN ('repository_sync','code_index') AND repository_id IS NOT NULL)
    OR (kind = 'jira_sync' AND jira_connection_id IS NOT NULL)
    OR (kind = 'server_test' AND server_id IS NOT NULL)
    OR (kind = 'rag_folder_index' AND rag_folder_id IS NOT NULL)
    OR (kind = 'rag_collection_sync' AND rag_collection_id IS NOT NULL));
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

-- Agents are granted whole collections.
CREATE TABLE agent_collections (
    agent_id text NOT NULL REFERENCES agents(id),
    collection_id uuid NOT NULL REFERENCES rag_collections(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, collection_id)
);
INSERT INTO agent_collections(agent_id, collection_id)
SELECT DISTINCT a.agent_id, c.id FROM agent_knowledge_folders a
JOIN rag_folders f ON f.id = a.folder_id
JOIN rag_collections c ON c.project_id = f.project_id AND c.deleted_at IS NULL
    AND c.source_path = 'rag-data/' || split_part(f.path, '/', 1);
DROP TABLE agent_knowledge_folders;
ALTER TABLE agents RENAME COLUMN all_folders TO all_collections;
ALTER TABLE agents DROP COLUMN include_subfolders;
