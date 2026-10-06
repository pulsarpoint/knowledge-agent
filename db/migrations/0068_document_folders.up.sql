SET search_path = ka, public;

-- A document can live in a documentation folder under GENERATED_DOCS_FOLDER (<folder>/<slug>/README.md); documents
-- without one export as before (<slug>/README.md). Same segment rule as RAG folders; services/ holds phase exports.
ALTER TABLE documents ADD COLUMN folder text CONSTRAINT documents_folder_check CHECK (
    length(folder) <= 200 AND folder ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$'
    AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services');

-- Slugs are unique per folder; NULLS NOT DISTINCT keeps folderless slugs unique across the project, as before.
ALTER TABLE documents DROP CONSTRAINT documents_project_id_slug_key;
ALTER TABLE documents ADD CONSTRAINT documents_project_id_folder_slug_key UNIQUE NULLS NOT DISTINCT (project_id, folder, slug);

-- d.* is expanded when a view is created, so the view is rebuilt to include the new column.
DROP VIEW document_details;
CREATE VIEW document_details AS
SELECT d.*, v.version, v.title, v.description, v.purpose, v.audience, v.tags, v.content_hash,
    COALESCE((SELECT array_agg(code_project_id ORDER BY code_project_id) FROM document_code_projects WHERE document_id=d.id),'{}') AS code_project_ids,
    COALESCE((SELECT array_agg(service_id ORDER BY service_id) FROM document_services WHERE document_id=d.id),'{}') AS service_ids,
    COALESCE((SELECT array_agg(server_id ORDER BY server_id) FROM document_servers WHERE document_id=d.id),'{}') AS server_ids,
    COALESCE((SELECT array_agg(deployment_id ORDER BY deployment_id) FROM document_deployments WHERE document_id=d.id),'{}') AS deployment_ids,
    COALESCE((SELECT array_agg(environment_id ORDER BY environment_id) FROM document_environments WHERE document_id=d.id),'{}') AS environment_ids
FROM documents d JOIN document_versions v ON v.id=d.current_version_id;
