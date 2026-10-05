SET search_path = ka, public;

ALTER TABLE agents ADD COLUMN include_subfolders boolean NOT NULL DEFAULT true;
ALTER TABLE agents RENAME COLUMN all_collections TO all_folders;
CREATE TABLE agent_knowledge_folders (
    agent_id text NOT NULL REFERENCES agents(id), folder_id uuid NOT NULL REFERENCES rag_folders(id) ON DELETE CASCADE,
    PRIMARY KEY(agent_id, folder_id)
);
-- Collections outside rag-data (and new ones) get a folder row so grants and files survive.
INSERT INTO rag_folders(project_id, path, description)
SELECT c.project_id, substr(c.source_path, 10), c.description FROM rag_collections c
WHERE c.deleted_at IS NULL AND c.source_path LIKE 'rag-data/%' AND position('/' in substr(c.source_path, 10)) = 0
  AND NOT EXISTS (SELECT 1 FROM rag_folders d WHERE d.project_id = c.project_id AND d.path = substr(c.source_path, 10) AND d.deleted_at IS NULL);
INSERT INTO agent_knowledge_folders(agent_id, folder_id)
SELECT DISTINCT a.agent_id, d.id FROM agent_collections a JOIN rag_collections c ON c.id = a.collection_id
JOIN rag_folders d ON d.project_id = c.project_id AND d.deleted_at IS NULL AND 'rag-data/' || d.path = c.source_path;
DROP TABLE agent_collections;

DROP INDEX rag_collection_one_active_job;
UPDATE jobs SET status = 'cancelled', finished_at = now(), error = 'RAG collections were rolled back',
    worker_id = NULL, lease_token = NULL, lease_expires_at = NULL
WHERE kind = 'rag_collection_sync' AND status IN ('queued', 'running', 'waiting_input');
CREATE OR REPLACE FUNCTION guard_job_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'job history cannot be deleted' USING ERRCODE = '23514'; END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN RAISE EXCEPTION 'terminal jobs are immutable' USING ERRCODE = '23514'; END IF;
    IF ROW(NEW.project_id, NEW.kind, NEW.repository_id, NEW.jira_connection_id, NEW.server_id, NEW.rag_folder_id)
        IS DISTINCT FROM ROW(OLD.project_id, OLD.kind, OLD.repository_id, OLD.jira_connection_id, OLD.server_id, OLD.rag_folder_id) THEN
        RAISE EXCEPTION 'job targets are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
-- Collection sync history cannot be represented without its column; such rows block rollback by design.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM jobs WHERE kind = 'rag_collection_sync') THEN
        RAISE EXCEPTION 'RAG collection sync history exists; restore a backup taken before 0063 to roll back';
    END IF;
END $$;
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check, DROP CONSTRAINT jobs_check1;
ALTER TABLE jobs DROP COLUMN rag_collection_id;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN
    ('repository_sync','code_index','documentation_generate','jira_sync','server_test','rag_folder_index'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check CHECK (
    num_nonnulls(repository_id, jira_connection_id, server_id, rag_folder_id) =
    CASE WHEN kind = 'documentation_generate' THEN 0 ELSE 1 END);
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    kind = 'documentation_generate' OR (kind IN ('repository_sync','code_index') AND repository_id IS NOT NULL)
    OR (kind = 'jira_sync' AND jira_connection_id IS NOT NULL)
    OR (kind = 'server_test' AND server_id IS NOT NULL)
    OR (kind = 'rag_folder_index' AND rag_folder_id IS NOT NULL));
CREATE UNIQUE INDEX rag_one_active_job ON jobs(project_id) WHERE kind = 'rag_folder_index' AND status IN ('queued', 'running', 'waiting_input');

UPDATE rag_workspaces w SET settings = jsonb_build_object(
    'max_file_bytes', coalesce((w.settings->>'max_file_bytes')::int, 2000000),
    'chunk_size', coalesce(c.chunk_size, 2000), 'chunk_overlap', coalesce(c.chunk_overlap, 200),
    'extensions', '[".md", ".markdown", ".txt", ".json"]'::jsonb,
    'embedding_enabled', coalesce(c.embeddings_enabled, false)),
    revision = w.revision + 1, updated_at = now()
FROM (SELECT w2.project_id, c2.chunk_size, c2.chunk_overlap, c2.embeddings_enabled FROM rag_workspaces w2
      LEFT JOIN LATERAL (SELECT * FROM rag_collections c3 WHERE c3.project_id = w2.project_id AND c3.deleted_at IS NULL
                         ORDER BY c3.created_at LIMIT 1) c2 ON true) c
WHERE c.project_id = w.project_id;

ALTER TABLE rag_files DROP CONSTRAINT rag_files_collection_path, DROP CONSTRAINT rag_files_collection_fk;
ALTER TABLE rag_files ADD COLUMN folder_id uuid, ADD COLUMN kind text NOT NULL DEFAULT 'file' CHECK (kind IN ('file', 'folder'));
UPDATE rag_files f SET folder_id = d.id, path = substr(c.source_path, 10) || '/' || f.path
FROM rag_collections c JOIN rag_folders d ON d.project_id = c.project_id AND d.deleted_at IS NULL AND 'rag-data/' || d.path = c.source_path
WHERE c.id = f.collection_id;
DELETE FROM rag_files WHERE folder_id IS NULL;
ALTER TABLE rag_files ALTER COLUMN kind DROP DEFAULT, ALTER COLUMN folder_id SET NOT NULL, DROP COLUMN collection_id,
    ADD FOREIGN KEY (folder_id, project_id) REFERENCES rag_folders(id, project_id),
    ADD UNIQUE (project_id, path, kind);
CREATE INDEX rag_files_folder ON rag_files(folder_id);
DROP TABLE rag_collections;
