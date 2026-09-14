BEGIN;
SET search_path = ka, public;
-- No v3 runtime consumes the old plugin/collection pipeline. Never discard legacy records.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM document_sets) OR EXISTS (SELECT 1 FROM rag_collections)
       OR EXISTS (SELECT 1 FROM jobs WHERE kind IN ('documentation_generate', 'rag_index')) THEN
        RAISE EXCEPTION 'Legacy documentation or RAG records exist; migrate them before applying this migration';
    END IF;
END $$;
DROP FUNCTION publish_rag_generation(uuid, uuid);
DROP TRIGGER guard_rag_activation ON rag_collections;
DROP TRIGGER guard_rag_snapshot ON rag_generations;
DROP TRIGGER guard_rag_snapshot ON rag_generation_documents;
DROP FUNCTION guard_rag_activation();
DROP FUNCTION guard_rag_snapshot();
ALTER TABLE rag_collections DROP CONSTRAINT rag_collections_active_generation;
DROP TABLE rag_generation_documents, rag_generations;
ALTER TABLE document_files DROP CONSTRAINT document_files_current_revision;
DROP TABLE document_revisions, document_files, documentation_generation_sources, documentation_generations;
DROP FUNCTION guard_documentation_snapshot();
ALTER TABLE jobs DROP COLUMN document_set_id, DROP COLUMN rag_collection_id;
DROP TABLE document_set_repositories, rag_collections, document_sets;
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN
    ('repository_sync','documentation_generate','jira_sync','server_test','rag_folder_index'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check CHECK (
    num_nonnulls(repository_id,jira_connection_id,server_id,rag_folder_id) =
    CASE WHEN kind='documentation_generate' THEN 0 ELSE 1 END);
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    kind='documentation_generate' OR (kind='repository_sync' AND repository_id IS NOT NULL)
    OR (kind='jira_sync' AND jira_connection_id IS NOT NULL)
    OR (kind='server_test' AND server_id IS NOT NULL)
    OR (kind='rag_folder_index' AND rag_folder_id IS NOT NULL));
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

COMMIT;
