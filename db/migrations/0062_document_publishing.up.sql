SET search_path = ka, public;

-- Where a document is published in the RAG knowledge base and whether indexing still has to run.
ALTER TABLE documents
    ADD COLUMN published_path text CHECK (published_path ~ '^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*/[a-z0-9]+(-[a-z0-9]+)*\.md$' AND length(published_path) <= 640),
    ADD COLUMN published_at timestamptz,
    ADD COLUMN publish_error text CHECK (length(publish_error) <= 2000),
    ADD COLUMN index_pending boolean NOT NULL DEFAULT false;

-- d.* is expanded when a view is created, so the view is rebuilt to include the new columns.
DROP VIEW document_details;
CREATE VIEW document_details AS
SELECT d.*, pv.version AS published_version, v.version, v.title, v.description, v.purpose, v.audience, v.tags, v.content_hash,
    COALESCE((SELECT array_agg(code_project_id ORDER BY code_project_id) FROM document_code_projects WHERE document_id=d.id),'{}') AS code_project_ids,
    COALESCE((SELECT array_agg(service_id ORDER BY service_id) FROM document_services WHERE document_id=d.id),'{}') AS service_ids,
    COALESCE((SELECT array_agg(server_id ORDER BY server_id) FROM document_servers WHERE document_id=d.id),'{}') AS server_ids,
    COALESCE((SELECT array_agg(deployment_id ORDER BY deployment_id) FROM document_deployments WHERE document_id=d.id),'{}') AS deployment_ids,
    COALESCE((SELECT array_agg(environment_id ORDER BY environment_id) FROM document_environments WHERE document_id=d.id),'{}') AS environment_ids
FROM documents d JOIN document_versions v ON v.id=d.current_version_id
LEFT JOIN document_versions pv ON pv.id=d.published_version_id;
