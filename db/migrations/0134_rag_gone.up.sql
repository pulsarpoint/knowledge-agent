BEGIN;
SET search_path = ka, public;

-- RAG is gone: its jobs (with their events and questions), its tables and the collection resource type. Job history
-- is otherwise immutable; the RAG kinds leave with the tables they pointed at.
ALTER TABLE job_events DISABLE TRIGGER reject_mutation;
DELETE FROM job_events e USING jobs j WHERE e.job_id = j.id AND j.kind IN ('rag_folder_index', 'rag_collection_sync');
ALTER TABLE job_events ENABLE TRIGGER reject_mutation;
DELETE FROM job_questions q USING jobs j WHERE q.job_id = j.id AND j.kind IN ('rag_folder_index', 'rag_collection_sync');
DROP TABLE rag_vectors, rag_chunks, rag_file_log, rag_files;
ALTER TABLE jobs DISABLE TRIGGER guard_job_update;
DELETE FROM jobs WHERE kind IN ('rag_folder_index', 'rag_collection_sync');
ALTER TABLE jobs ENABLE TRIGGER guard_job_update;

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check, DROP CONSTRAINT jobs_check1;
DROP INDEX rag_collection_one_active_job;
ALTER TABLE jobs DROP COLUMN rag_folder_id, DROP COLUMN rag_collection_id;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN ('repository_sync', 'code_index', 'jira_sync', 'server_test'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check CHECK (num_nonnulls(repository_id, jira_connection_id, server_id) = 1);
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    (kind IN ('repository_sync', 'code_index') AND repository_id IS NOT NULL) OR
    (kind = 'jira_sync' AND jira_connection_id IS NOT NULL) OR
    (kind = 'server_test' AND server_id IS NOT NULL)
);
CREATE OR REPLACE FUNCTION guard_job_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'job history cannot be deleted' USING ERRCODE = '23514'; END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN RAISE EXCEPTION 'terminal jobs are immutable' USING ERRCODE = '23514'; END IF;
    IF ROW(NEW.project_id, NEW.kind, NEW.repository_id, NEW.jira_connection_id, NEW.server_id)
        IS DISTINCT FROM ROW(OLD.project_id, OLD.kind, OLD.repository_id, OLD.jira_connection_id, OLD.server_id) THEN
        RAISE EXCEPTION 'job targets are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

DROP TABLE rag_collection_paths, rag_collections, rag_folders, rag_workspaces;

-- A model change no longer bumps the RAG workspace; the chat/embeddings check stays.
CREATE OR REPLACE FUNCTION embedding_model_updated() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.api_format='embeddings')<>(NEW.api_format='embeddings') THEN
        RAISE EXCEPTION 'Create a separate connection when changing between chat and embeddings';
    END IF;
    RETURN NEW;
END $$;

-- Stored access without collection entries: saved agents, runs, and the access copy in a run's agent_config.
CREATE OR REPLACE FUNCTION pg_temp.without_collections(resources jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(jsonb_agg(item ORDER BY position), '[]'::jsonb)
    FROM jsonb_array_elements(resources) WITH ORDINALITY AS x(item, position)
    WHERE item->>'type' IS DISTINCT FROM 'collection'
$$;
UPDATE agents SET access = jsonb_set(access, '{resources}', pg_temp.without_collections(access->'resources'))
WHERE access->'resources' @> '[{"type": "collection"}]';
UPDATE agent_sessions SET access = jsonb_set(access, '{resources}', pg_temp.without_collections(access->'resources'))
WHERE access->'resources' @> '[{"type": "collection"}]';
UPDATE agent_sessions
SET agent_config = jsonb_set(agent_config, '{access,resources}',
                             pg_temp.without_collections(agent_config->'access'->'resources'))
WHERE jsonb_typeof(agent_config->'access'->'resources') = 'array'
  AND agent_config->'access'->'resources' @> '[{"type": "collection"}]';

COMMIT;
