BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Back to 0108: services.operations_notes comes back empty; documents.inventory, its commit-time check and the trigger
-- clearing it come back, set on the live documents and brand-new drafts tagged `inventory` built for exactly one
-- server, service or environment (the tags stay). Refused when two such documents are built for one resource.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM documents d JOIN document_resources r ON r.document_id = d.id
        WHERE d.draft_of IS NULL AND 'inventory' = ANY(d.tags) AND r.connection = 'built_for' AND r.removed_at IS NULL
          AND r.resource_type IN ('server', 'service', 'environment')
        GROUP BY COALESCE(r.server_id, r.service_id, r.environment_id) HAVING count(DISTINCT d.id) > 1
    ) THEN
        RAISE EXCEPTION 'Several documents tagged inventory are built for one resource and their inventory flag would be lost; remove the tag from all but one of them before rollback';
    END IF;
END $$;

DROP VIEW service_assets;
ALTER TABLE services ADD COLUMN operations_notes text NOT NULL DEFAULT '';
COMMENT ON COLUMN services.operations_notes IS 'Untrusted operator context, never executable instructions or stored credentials.';

CREATE VIEW service_assets AS
SELECT s.id, s.project_id, s.name, s.description, s.owner, s.operations_notes,
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

DROP VIEW document_drafts_view;
DROP VIEW document_details;
DROP VIEW live_documents;

ALTER TABLE documents
    ADD COLUMN inventory boolean NOT NULL DEFAULT false,
    -- A draft of a live document carries no links: it reports its document's flag and never has its own.
    ADD CONSTRAINT documents_inventory_owner CHECK (NOT inventory OR draft_of IS NULL);
COMMENT ON COLUMN documents.inventory IS
    'The inventory document of the one server, service or environment it is built for (at most one per resource among live documents and brand-new drafts).';

ALTER TABLE documents DISABLE TRIGGER touch_updated_at;
UPDATE documents d SET inventory = true
WHERE d.draft_of IS NULL AND 'inventory' = ANY(d.tags)
  AND (SELECT count(*) FROM document_resources r WHERE r.document_id = d.id AND r.connection = 'built_for'
       AND r.removed_at IS NULL) = 1
  AND EXISTS (SELECT 1 FROM document_resources r WHERE r.document_id = d.id AND r.connection = 'built_for'
       AND r.removed_at IS NULL AND r.resource_type IN ('server', 'service', 'environment'));
ALTER TABLE documents ENABLE TRIGGER touch_updated_at;

-- Checked at commit: an inventory document has exactly one active built_for link, to a server, a service or an
-- environment, and no other inventory document (live or brand-new draft) is built for that resource. A lock per
-- resource serializes the check, so two transactions cannot each add one.
CREATE FUNCTION inventory_document_check(document uuid) RETURNS void LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    link record;
    links integer;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM documents WHERE id = document AND inventory) THEN
        RETURN;
    END IF;
    SELECT count(*) INTO links FROM document_resources
        WHERE document_id = document AND connection = 'built_for' AND removed_at IS NULL;
    SELECT r.resource_type, COALESCE(r.server_id, r.service_id, r.environment_id) AS resource_id INTO link
        FROM document_resources r
        WHERE r.document_id = document AND r.connection = 'built_for' AND r.removed_at IS NULL
          AND r.resource_type IN ('server', 'service', 'environment');
    IF links <> 1 OR link.resource_id IS NULL THEN
        RAISE EXCEPTION 'An inventory document must be built for one server, service or environment.';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('inventory-document:' || link.resource_id::text));
    IF EXISTS (
        SELECT 1 FROM documents d JOIN document_resources r ON r.document_id = d.id
        WHERE d.inventory AND d.draft_of IS NULL AND d.id <> document AND r.connection = 'built_for'
          AND r.removed_at IS NULL AND COALESCE(r.server_id, r.service_id, r.environment_id) = link.resource_id
    ) THEN
        RAISE EXCEPTION '% % already has an inventory document.', initcap(link.resource_type),
            (SELECT name::text FROM servers WHERE id = link.resource_id UNION ALL
             SELECT name::text FROM services WHERE id = link.resource_id UNION ALL
             SELECT name::text FROM environments WHERE id = link.resource_id LIMIT 1);
    END IF;
END $$;

CREATE FUNCTION documents_inventory_check() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    PERFORM inventory_document_check(NEW.id);
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER documents_inventory AFTER INSERT OR UPDATE OF inventory ON documents
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.inventory)
    EXECUTE FUNCTION documents_inventory_check();

CREATE FUNCTION document_resources_inventory_check() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    PERFORM inventory_document_check(NEW.document_id);
    RETURN NULL;
END $$;
-- Links are added and removed, never deleted (a deletion is the document or the resource going away).
CREATE CONSTRAINT TRIGGER document_resources_inventory AFTER INSERT OR UPDATE ON document_resources
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION document_resources_inventory_check();

-- A resource deleted (its built-for link cascades away) leaves no inventory document behind: the flag is cleared.
CREATE FUNCTION document_resources_inventory_gone() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    UPDATE documents SET inventory = false WHERE id = OLD.document_id AND inventory;
    RETURN NULL;
END $$;
CREATE TRIGGER document_resources_inventory_gone AFTER DELETE ON document_resources
    FOR EACH ROW WHEN (OLD.connection = 'built_for' AND OLD.removed_at IS NULL)
    EXECUTE FUNCTION document_resources_inventory_gone();

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
    (SELECT count(*) FROM documents x WHERE x.draft_of = d.id)::integer AS draft_count,
    d.inventory
FROM documents d WHERE NOT d.is_draft;

-- A draft of a live document reports its document's flag.
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
        '{}') AS environment_ids,
    COALESCE(l.inventory, x.inventory) AS inventory
FROM documents x LEFT JOIN documents l ON l.id = x.draft_of
WHERE x.is_draft;

COMMIT;
