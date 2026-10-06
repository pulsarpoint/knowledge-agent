SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM documents GROUP BY project_id, slug HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'Documents in different folders share a slug; rename or delete them before rolling back 0068';
    END IF;
END $$;

DROP VIEW document_details;
ALTER TABLE documents DROP CONSTRAINT documents_project_id_folder_slug_key;
ALTER TABLE documents DROP COLUMN folder;
ALTER TABLE documents ADD CONSTRAINT documents_project_id_slug_key UNIQUE (project_id, slug);
CREATE VIEW document_details AS
SELECT d.*, v.version, v.title, v.description, v.purpose, v.audience, v.tags, v.content_hash,
    COALESCE((SELECT array_agg(code_project_id ORDER BY code_project_id) FROM document_code_projects WHERE document_id=d.id),'{}') AS code_project_ids,
    COALESCE((SELECT array_agg(service_id ORDER BY service_id) FROM document_services WHERE document_id=d.id),'{}') AS service_ids,
    COALESCE((SELECT array_agg(server_id ORDER BY server_id) FROM document_servers WHERE document_id=d.id),'{}') AS server_ids,
    COALESCE((SELECT array_agg(deployment_id ORDER BY deployment_id) FROM document_deployments WHERE document_id=d.id),'{}') AS deployment_ids,
    COALESCE((SELECT array_agg(environment_id ORDER BY environment_id) FROM document_environments WHERE document_id=d.id),'{}') AS environment_ids
FROM documents d JOIN document_versions v ON v.id=d.current_version_id;
