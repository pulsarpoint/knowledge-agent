BEGIN;
SET search_path=ka,public;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM documents) OR EXISTS (SELECT 1 FROM documentation_prompts)
       OR EXISTS (SELECT 1 FROM documentation_generations) THEN
        RAISE EXCEPTION 'Documentation data exists; export/migrate it before rollback';
    END IF;
END $$;
DROP VIEW document_details;
DROP TABLE document_code_projects,document_services,document_servers,document_deployments;
ALTER TABLE service_deployments DROP CONSTRAINT service_deployments_id_project_key;
ALTER TABLE service_deployments DROP CONSTRAINT service_deployments_service_id_server_id_environment_runtim_key;
ALTER TABLE service_deployments ADD CONSTRAINT service_deployments_service_id_server_id_environment_runtim_key
    UNIQUE (service_id,server_id,environment,runtime_name);
ALTER TABLE documents DROP CONSTRAINT documents_current_version_fk, DROP CONSTRAINT documents_published_version_fk;
DROP TABLE document_versions,documents;
DROP TABLE documentation_generations;
DROP FUNCTION check_documentation_job();
ALTER TABLE documentation_prompts DROP CONSTRAINT documentation_prompts_active_version_fk;
DROP TABLE documentation_prompt_versions,documentation_prompts;
DROP FUNCTION guard_documentation_prompt_version();
COMMIT;
