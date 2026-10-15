BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Tags instead of special columns. The inventory document of a server, service or environment is the document tagged
-- `inventory` built for it (documents.tags); nothing in the database enforces one per resource. Operating notes on a
-- service belong in documents: services.operations_notes goes (test data: the notes are not converted).

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

-- A flagged document and its drafts (a draft of a live document reported its document's flag; publishing it must keep
-- the tag) get the tag. Adding a tag is not an edit: updated_at stays.
ALTER TABLE documents DISABLE TRIGGER touch_updated_at;
UPDATE documents SET tags = tags || 'inventory'::text
WHERE NOT 'inventory' = ANY(tags)
  AND (inventory OR draft_of IN (SELECT id FROM documents WHERE inventory));
ALTER TABLE documents ENABLE TRIGGER touch_updated_at;
ALTER TABLE documents DROP CONSTRAINT documents_inventory_owner, DROP COLUMN inventory;

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

DROP VIEW service_assets;
ALTER TABLE services DROP COLUMN operations_notes;

CREATE VIEW service_assets AS
SELECT s.id, s.project_id, s.name, s.description, s.owner,
    s.revision, s.created_at, s.updated_at,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', r.id, 'name', r.name, 'remote_url', r.remote_url,
        'requested_ref', r.requested_ref, 'current_commit', r.current_commit,
        'checkout_path', r.checkout_path, 'synced_at', r.synced_at,
        'archived', r.archived_at IS NOT NULL) ORDER BY lower(r.name), r.id)
        FROM inventory_links l JOIN repositories r ON r.id = l.to_code_project_id
        WHERE l.kind = 'built_from' AND l.from_service_id = s.id), '[]'::jsonb) AS repositories,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', d.id, 'server_id', d.server_id, 'environment_id', d.environment_id, 'environment', COALESCE(e.name,d.environment),
        'runtime', d.runtime, 'runtime_name', d.runtime_name, 'port', d.port,
        'log_location', d.log_location, 'deployed_version', d.deployed_version,
        'server', to_jsonb(h) - 'project_id')
        ORDER BY COALESCE(e.name,d.environment), h.name, d.runtime_name, d.id)
        FROM service_deployments d JOIN server_assets h ON h.id=d.server_id
        LEFT JOIN environments e ON e.id=d.environment_id
        WHERE d.service_id=s.id), '[]'::jsonb) AS deployments,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'service_id',other.id,'name',other.name,'description',l.description)
        ORDER BY lower(other.name),other.id)
        FROM inventory_links l JOIN services other ON other.id=l.to_service_id
        WHERE l.kind='depends_on' AND l.from_service_id=s.id),'[]'::jsonb) AS dependencies,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'service_id',other.id,'name',other.name,'description',l.description)
        ORDER BY lower(other.name),other.id)
        FROM inventory_links l JOIN services other ON other.id=l.from_service_id
        WHERE l.kind='depends_on' AND l.to_service_id=s.id),'[]'::jsonb) AS dependents,
    s.service_type, s.service_role,
    COALESCE((SELECT jsonb_agg(jsonb_build_object('id',e.id,'name',e.name) ORDER BY lower(e.name),e.id)
        FROM environment_services es JOIN environments e ON e.id=es.environment_id
        WHERE es.service_id=s.id),'[]'::jsonb) AS environments,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', l.id, 'direction', CASE WHEN l.from_id = s.id THEN 'outgoing' ELSE 'incoming' END,
        'kind', l.kind, 'entry_kind', o.kind, 'entry_id', o.id, 'name', o.name,
        'description', l.description, 'attributes', l.attributes)
        ORDER BY lower(o.name), o.id, l.id)
        FROM inventory_links l
        JOIN inventory_entries o ON o.id = CASE WHEN l.from_id = s.id THEN l.to_id ELSE l.from_id END
        WHERE l.kind = 'connects_to' AND (l.from_id = s.id OR l.to_id = s.id)), '[]'::jsonb) AS links
FROM services s;

COMMENT ON VIEW service_assets IS 'Scoped service context with repository snapshots and server inventory; excludes authentication secrets.';

COMMIT;
