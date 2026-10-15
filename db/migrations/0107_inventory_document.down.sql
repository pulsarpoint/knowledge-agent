BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Back to 0106: the inventory flag goes (inventory documents become ordinary documents, their links stay) and
-- inventory_documents comes back empty, as 0095 defines it with 0104's live-document trigger.

DROP VIEW document_drafts_view;
DROP VIEW document_details;
DROP VIEW live_documents;

DROP TRIGGER document_resources_inventory_gone ON document_resources;
DROP FUNCTION document_resources_inventory_gone();
DROP TRIGGER document_resources_inventory ON document_resources;
DROP TRIGGER documents_inventory ON documents;
DROP FUNCTION document_resources_inventory_check();
DROP FUNCTION documents_inventory_check();
DROP FUNCTION inventory_document_check(uuid);
ALTER TABLE documents DROP CONSTRAINT documents_inventory_owner, DROP COLUMN inventory;

-- Documents attached to an inventory entry. The documents live in ka.documents; detaching never deletes them.
CREATE TABLE inventory_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    service_id uuid,
    server_id uuid,
    environment_id uuid,
    code_project_id uuid,
    document_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    entry_kind text GENERATED ALWAYS AS (CASE WHEN service_id IS NOT NULL THEN 'service'
        WHEN server_id IS NOT NULL THEN 'server' WHEN environment_id IS NOT NULL THEN 'environment'
        ELSE 'code_project' END) STORED,
    entry_id uuid GENERATED ALWAYS AS (COALESCE(service_id, server_id, environment_id, code_project_id)) STORED,
    CONSTRAINT inventory_documents_one_entry CHECK (num_nonnulls(service_id, server_id, environment_id, code_project_id) = 1),
    FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX inventory_documents_once ON inventory_documents (entry_id, document_id);
CREATE INDEX inventory_documents_document ON inventory_documents (document_id);
CREATE INDEX inventory_documents_project ON inventory_documents (project_id);
CREATE TRIGGER inventory_documents_live BEFORE INSERT OR UPDATE ON inventory_documents
    FOR EACH ROW EXECUTE FUNCTION refuse_draft_document('document_id');

CREATE VIEW live_documents AS SELECT * FROM documents WHERE NOT is_draft;

CREATE VIEW document_details AS
SELECT d.id, d.project_id, d.slug, d.folder, d.revision, d.created_at, d.updated_at, d.phase_metadata,
    d.version, d.title, d.description, d.purpose, d.audience, d.tags, d.content_hash, d.origin, d.from_draft,
    d.version_created_at, d.version_created_by, d.created_by, d.updated_by,
    COALESCE((SELECT array_agg(r.code_project_id ORDER BY r.code_project_id) FROM document_resources r
        WHERE r.document_id = d.id AND r.removed_at IS NULL AND r.code_project_id IS NOT NULL), '{}') AS code_project_ids,
    COALESCE((SELECT array_agg(r.service_id ORDER BY r.service_id) FROM document_resources r
        WHERE r.document_id = d.id AND r.removed_at IS NULL AND r.service_id IS NOT NULL), '{}') AS service_ids,
    COALESCE((SELECT array_agg(r.server_id ORDER BY r.server_id) FROM document_resources r
        WHERE r.document_id = d.id AND r.removed_at IS NULL AND r.server_id IS NOT NULL), '{}') AS server_ids,
    COALESCE((SELECT array_agg(r.deployment_id ORDER BY r.deployment_id) FROM document_resources r
        WHERE r.document_id = d.id AND r.removed_at IS NULL AND r.deployment_id IS NOT NULL), '{}') AS deployment_ids,
    COALESCE((SELECT array_agg(r.environment_id ORDER BY r.environment_id) FROM document_resources r
        WHERE r.document_id = d.id AND r.removed_at IS NULL AND r.environment_id IS NOT NULL), '{}') AS environment_ids,
    (SELECT count(*) FROM documents x WHERE x.draft_of = d.id)::integer AS draft_count
FROM documents d WHERE NOT d.is_draft;

CREATE VIEW document_drafts_view AS
SELECT x.id, x.project_id, x.draft_of AS document_id, COALESCE(x.slug, l.slug) AS slug,
    COALESCE(x.folder, l.folder) AS folder, x.title, x.description, x.purpose, x.audience, x.tags, x.markdown,
    x.content_hash, x.origin, x.session_id, x.edited, x.revision, x.base_version, l.version AS live_version,
    l.title AS document_title, (x.draft_of IS NOT NULL AND x.base_version IS DISTINCT FROM l.version) AS stale,
    x.created_by, x.updated_by, x.created_at, x.updated_at,
    COALESCE((SELECT array_agg(r.code_project_id ORDER BY r.code_project_id) FROM document_resources r
        WHERE r.document_id = COALESCE(x.draft_of, x.id) AND r.removed_at IS NULL AND r.code_project_id IS NOT NULL),
        '{}') AS code_project_ids,
    COALESCE((SELECT array_agg(r.service_id ORDER BY r.service_id) FROM document_resources r
        WHERE r.document_id = COALESCE(x.draft_of, x.id) AND r.removed_at IS NULL AND r.service_id IS NOT NULL),
        '{}') AS service_ids,
    COALESCE((SELECT array_agg(r.server_id ORDER BY r.server_id) FROM document_resources r
        WHERE r.document_id = COALESCE(x.draft_of, x.id) AND r.removed_at IS NULL AND r.server_id IS NOT NULL),
        '{}') AS server_ids,
    COALESCE((SELECT array_agg(r.deployment_id ORDER BY r.deployment_id) FROM document_resources r
        WHERE r.document_id = COALESCE(x.draft_of, x.id) AND r.removed_at IS NULL AND r.deployment_id IS NOT NULL),
        '{}') AS deployment_ids,
    COALESCE((SELECT array_agg(r.environment_id ORDER BY r.environment_id) FROM document_resources r
        WHERE r.document_id = COALESCE(x.draft_of, x.id) AND r.removed_at IS NULL AND r.environment_id IS NOT NULL),
        '{}') AS environment_ids
FROM documents x LEFT JOIN documents l ON l.id = x.draft_of
WHERE x.is_draft;

COMMIT;
