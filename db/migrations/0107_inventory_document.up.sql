BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- One inventory document per server, service or environment: a document with documents.inventory, built for that
-- resource. Documents of a resource come from their links (document_resources); attachments (inventory_documents) are
-- dropped. Their rows are test data and are not carried over.

ALTER TABLE documents
    ADD COLUMN inventory boolean NOT NULL DEFAULT false,
    -- A draft of a live document carries no links: it reports its document's flag and never has its own.
    ADD CONSTRAINT documents_inventory_owner CHECK (NOT inventory OR draft_of IS NULL);
COMMENT ON COLUMN documents.inventory IS
    'The inventory document of the one server, service or environment it is built for (at most one per resource among live documents and brand-new drafts).';

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

DROP TABLE inventory_documents;

CREATE OR REPLACE VIEW live_documents AS SELECT * FROM documents WHERE NOT is_draft;

CREATE OR REPLACE VIEW document_details AS
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
CREATE OR REPLACE VIEW document_drafts_view AS
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
