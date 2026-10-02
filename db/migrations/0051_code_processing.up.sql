BEGIN;
SET search_path=ka,public;
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check1;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN
    ('repository_sync','code_index','documentation_generate','jira_sync','server_test','rag_folder_index'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    kind='documentation_generate' OR (kind IN ('repository_sync','code_index') AND repository_id IS NOT NULL)
    OR (kind='jira_sync' AND jira_connection_id IS NOT NULL)
    OR (kind='server_test' AND server_id IS NOT NULL)
    OR (kind='rag_folder_index' AND rag_folder_id IS NOT NULL));
COMMIT;
