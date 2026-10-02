BEGIN;
SET search_path=ka,public;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM jobs WHERE kind='code_index') THEN
        RAISE EXCEPTION 'Code processing job history exists; preserve it before rollback';
    END IF;
END $$;
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check1;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN
    ('repository_sync','documentation_generate','jira_sync','server_test','rag_folder_index'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    kind='documentation_generate' OR (kind='repository_sync' AND repository_id IS NOT NULL)
    OR (kind='jira_sync' AND jira_connection_id IS NOT NULL)
    OR (kind='server_test' AND server_id IS NOT NULL)
    OR (kind='rag_folder_index' AND rag_folder_id IS NOT NULL));
COMMIT;
