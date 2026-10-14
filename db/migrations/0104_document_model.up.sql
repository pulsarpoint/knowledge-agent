BEGIN;
SET search_path = ka, public;
-- documents' deferred version foreign keys are checked per statement, so ALTER TABLE never meets pending checks.
SET CONSTRAINTS ALL IMMEDIATE;

-- Live documents and drafts in one table; earlier live contents in document_archive; links in document_resources.

-- 1. documents: content and draft columns.
ALTER TABLE documents
    ADD COLUMN is_draft boolean NOT NULL DEFAULT false,
    ADD COLUMN draft_of uuid,
    ADD COLUMN version integer CHECK (version > 0),
    ADD COLUMN base_version integer CHECK (base_version > 0),
    ADD COLUMN title nonblank, ADD COLUMN description text, ADD COLUMN purpose nonblank, ADD COLUMN audience text,
    ADD COLUMN tags text[], ADD COLUMN markdown nonblank, ADD COLUMN content_hash sha256,
    ADD COLUMN source_snapshot jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(source_snapshot) = 'object'),
    ADD COLUMN origin text, ADD COLUMN session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL,
    ADD COLUMN edited boolean NOT NULL DEFAULT false,
    ADD COLUMN from_draft boolean NOT NULL DEFAULT false,
    ADD COLUMN version_created_at timestamptz,
    ADD COLUMN version_created_by uuid REFERENCES principals(id),
    ADD COLUMN created_by uuid REFERENCES principals(id),
    ADD COLUMN updated_by uuid REFERENCES principals(id);

-- Moving content is not an edit: updated_at stays.
ALTER TABLE documents DISABLE TRIGGER touch_updated_at;
UPDATE documents d SET
    version = v.version, title = v.title, description = v.description, purpose = v.purpose, audience = v.audience,
    tags = v.tags, markdown = v.markdown, content_hash = v.content_hash, source_snapshot = v.source_snapshot,
    origin = v.origin, version_created_at = v.created_at, version_created_by = v.created_by,
    created_by = (SELECT f.created_by FROM document_versions f WHERE f.document_id = d.id ORDER BY f.version LIMIT 1),
    from_draft = EXISTS (SELECT 1 FROM document_drafts x WHERE x.promoted_version_id = v.id)
FROM document_versions v WHERE v.id = d.current_version_id;
ALTER TABLE documents ENABLE TRIGGER touch_updated_at;

-- 2. archive: every version that is not current, keeping its id.
CREATE TABLE document_archive (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    document_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    title nonblank NOT NULL CHECK (length(title) <= 240),
    description text NOT NULL CHECK (length(description) <= 4000),
    purpose nonblank NOT NULL CHECK (length(purpose) <= 80),
    audience text NOT NULL CHECK (audience IN ('people', 'agents', 'both')),
    tags text[] NOT NULL DEFAULT '{}',
    markdown nonblank NOT NULL CHECK (length(markdown) <= 1000000),
    content_hash sha256 NOT NULL CHECK (content_hash = encode(sha256(convert_to(markdown, 'UTF8')), 'hex')),
    source_snapshot jsonb NOT NULL CHECK (jsonb_typeof(source_snapshot) = 'object'),
    origin text NOT NULL CHECK (origin IN ('manual', 'generated', 'uploaded')),
    from_draft boolean NOT NULL DEFAULT false,
    session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL,
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL,
    archived_at timestamptz NOT NULL DEFAULT now(),
    archived_by uuid REFERENCES principals(id),
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE,
    UNIQUE (document_id, version)
);
CREATE TRIGGER document_archive_immutable BEFORE UPDATE ON document_archive
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
COMMENT ON TABLE document_archive IS
    'Earlier live contents of a document (document_id = the live document), one row per replaced version.';

INSERT INTO document_archive(id, project_id, document_id, version, title, description, purpose, audience, tags,
    markdown, content_hash, source_snapshot, origin, from_draft, created_by, created_at, archived_at)
SELECT v.id, v.project_id, v.document_id, v.version, v.title, v.description, v.purpose, v.audience, v.tags,
    v.markdown, v.content_hash, v.source_snapshot, v.origin,
    EXISTS (SELECT 1 FROM document_drafts x WHERE x.promoted_version_id = v.id),
    v.created_by, v.created_at,
    COALESCE((SELECT min(n.created_at) FROM document_versions n
              WHERE n.document_id = v.document_id AND n.version > v.version), v.created_at)
FROM document_versions v JOIN documents d ON d.id = v.document_id
WHERE v.id <> d.current_version_id;

-- 3. drafts become rows of documents. Slug uniqueness moves to a partial index first.
ALTER TABLE documents DROP CONSTRAINT documents_project_id_folder_slug_key;
-- Draft rows have no version: current_version_id (dropped in 7) must allow NULL until then.
ALTER TABLE documents ALTER COLUMN slug DROP NOT NULL, ALTER COLUMN current_version_id DROP NOT NULL;

-- Renaming drafts is not an edit either.
ALTER TABLE document_drafts DISABLE TRIGGER touch_updated_at;
-- A new-document draft named like a live document becomes a draft of it (as promote does today).
UPDATE document_drafts x SET document_id = d.id, slug = NULL, folder = NULL,
    base_version_id = d.current_version_id
FROM documents d
WHERE x.status = 'open' AND x.document_id IS NULL AND d.project_id = x.project_id
  AND d.slug = x.slug AND d.folder IS NOT DISTINCT FROM x.folder;
-- Two open new-document drafts with one name: the older keeps it, the others get -2, -3, ...
WITH numbered AS (
    SELECT id, row_number() OVER (PARTITION BY project_id, folder, slug ORDER BY created_at, id) AS n
    FROM document_drafts WHERE status = 'open' AND document_id IS NULL
)
UPDATE document_drafts x SET slug = left(x.slug, 116) || '-' || numbered.n
FROM numbered WHERE numbered.id = x.id AND numbered.n > 1;
ALTER TABLE document_drafts ENABLE TRIGGER touch_updated_at;

INSERT INTO documents(id, project_id, slug, folder, revision, created_at, updated_at, is_draft, draft_of,
    base_version, title, description, purpose, audience, tags, markdown, content_hash, origin, session_id, edited,
    created_by, updated_by)
SELECT x.id, x.project_id, x.slug, x.folder, x.revision, x.created_at, x.updated_at, true, x.document_id,
    (SELECT v.version FROM document_versions v WHERE v.id = x.base_version_id),
    x.title, x.description, x.purpose, x.audience, x.tags, x.markdown,
    encode(sha256(convert_to(x.markdown, 'UTF8')), 'hex'),
    CASE x.origin WHEN 'agent' THEN 'generated' ELSE 'manual' END, x.session_id, x.edited,
    x.created_by, x.updated_by
FROM document_drafts x WHERE x.status = 'open';

ALTER TABLE documents
    ALTER COLUMN title SET NOT NULL, ALTER COLUMN description SET NOT NULL, ALTER COLUMN purpose SET NOT NULL,
    ALTER COLUMN audience SET NOT NULL, ALTER COLUMN tags SET NOT NULL, ALTER COLUMN tags SET DEFAULT '{}',
    ALTER COLUMN markdown SET NOT NULL, ALTER COLUMN content_hash SET NOT NULL, ALTER COLUMN origin SET NOT NULL,
    ADD CONSTRAINT documents_title_length CHECK (length(title) <= 240),
    ADD CONSTRAINT documents_description_length CHECK (length(description) <= 4000),
    ADD CONSTRAINT documents_purpose_length CHECK (length(purpose) <= 80),
    ADD CONSTRAINT documents_audience_check CHECK (audience IN ('people', 'agents', 'both')),
    ADD CONSTRAINT documents_markdown_length CHECK (length(markdown) <= 1000000),
    ADD CONSTRAINT documents_content_hash_check
        CHECK (content_hash = encode(sha256(convert_to(markdown, 'UTF8')), 'hex')),
    ADD CONSTRAINT documents_origin_check CHECK (origin IN ('manual', 'generated', 'uploaded')),
    -- Drafts keep 0098's tag limit; live rows never had one (document_versions).
    ADD CONSTRAINT documents_draft_tags_limit CHECK (NOT is_draft OR cardinality(tags) <= 30),
    ADD CONSTRAINT documents_kind CHECK (
        (NOT is_draft AND draft_of IS NULL AND slug IS NOT NULL AND version IS NOT NULL
            AND version_created_at IS NOT NULL AND base_version IS NULL)
        OR (is_draft AND version IS NULL AND draft_of IS NULL AND slug IS NOT NULL AND base_version IS NULL)
        OR (is_draft AND version IS NULL AND draft_of IS NOT NULL AND slug IS NULL AND folder IS NULL)),
    ADD CONSTRAINT documents_draft_of_fk FOREIGN KEY (draft_of, project_id)
        REFERENCES documents(id, project_id) ON DELETE CASCADE;
CREATE UNIQUE INDEX documents_folder_slug ON documents(project_id, folder, slug) NULLS NOT DISTINCT
    WHERE slug IS NOT NULL;
CREATE INDEX documents_drafts ON documents(draft_of, created_at) WHERE is_draft;
CREATE INDEX documents_new_drafts ON documents(project_id, created_at) WHERE is_draft AND draft_of IS NULL;
CREATE INDEX documents_session ON documents(session_id) WHERE session_id IS NOT NULL;
-- A draft's outcome (promoted, discarded, replaced) is read from its audit rows once the draft row is gone.
CREATE INDEX audit_events_document_draft ON audit_events(resource_id, created_at DESC)
    WHERE resource_type = 'document_draft';
COMMENT ON TABLE documents IS
    'Live documents (is_draft false; version, content) and drafts (is_draft; draft_of a live document, or a brand-new document with its own slug). Earlier live contents are in document_archive.';

CREATE FUNCTION document_draft_of_live() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF NEW.draft_of IS NOT NULL AND NOT EXISTS (SELECT 1 FROM documents WHERE id = NEW.draft_of AND NOT is_draft) THEN
        RAISE EXCEPTION 'A draft can only revise a live document';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER document_draft_of_live BEFORE INSERT OR UPDATE OF draft_of ON documents
    FOR EACH ROW EXECUTE FUNCTION document_draft_of_live();

CREATE FUNCTION document_stays_live() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF NEW.is_draft AND NOT OLD.is_draft THEN
        RAISE EXCEPTION 'A live document cannot become a draft';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER document_stays_live BEFORE UPDATE OF is_draft ON documents
    FOR EACH ROW EXECUTE FUNCTION document_stays_live();

-- Tables that name a document must name a live one.
CREATE FUNCTION refuse_draft_document() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    col text;
    target uuid;
BEGIN
    FOREACH col IN ARRAY TG_ARGV LOOP
        target := (to_jsonb(NEW) ->> col)::uuid;
        IF target IS NOT NULL AND EXISTS (SELECT 1 FROM documents WHERE id = target AND is_draft) THEN
            RAISE EXCEPTION 'A draft is not a document yet; % must name a live document', col;
        END IF;
    END LOOP;
    RETURN NEW;
END $$;
CREATE TRIGGER document_links_live BEFORE INSERT OR UPDATE ON document_links
    FOR EACH ROW EXECUTE FUNCTION refuse_draft_document('from_document_id', 'to_document_id');
CREATE TRIGGER inventory_documents_live BEFORE INSERT OR UPDATE ON inventory_documents
    FOR EACH ROW EXECUTE FUNCTION refuse_draft_document('document_id');
CREATE TRIGGER agent_sessions_live_document BEFORE INSERT OR UPDATE OF document_id, target_document_id ON agent_sessions
    FOR EACH ROW EXECUTE FUNCTION refuse_draft_document('document_id', 'target_document_id');
CREATE TRIGGER discovery_proposals_live_document BEFORE INSERT OR UPDATE OF document_id ON discovery_proposals
    FOR EACH ROW EXECUTE FUNCTION refuse_draft_document('document_id');

-- 4. links with provenance and history.
CREATE TABLE document_resources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    document_id uuid NOT NULL,
    environment_id uuid,
    service_id uuid,
    server_id uuid,
    code_project_id uuid,
    deployment_id uuid,
    resource_type text NOT NULL GENERATED ALWAYS AS (CASE
        WHEN environment_id IS NOT NULL THEN 'environment'
        WHEN service_id IS NOT NULL THEN 'service'
        WHEN server_id IS NOT NULL THEN 'server'
        WHEN code_project_id IS NOT NULL THEN 'code_project'
        ELSE 'deployment' END) STORED,
    connection text NOT NULL CHECK (connection IN ('built_for', 'linked')),
    added_how text NOT NULL CHECK (added_how IN ('person', 'agent_run', 'discovery', 'migrated')),
    added_by uuid REFERENCES principals(id),
    session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    removed_at timestamptz,
    removed_by uuid REFERENCES principals(id),
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (deployment_id, project_id) REFERENCES service_deployments(id, project_id) ON DELETE CASCADE,
    CONSTRAINT document_resources_one_resource
        CHECK (num_nonnulls(environment_id, service_id, server_id, code_project_id, deployment_id) = 1),
    CONSTRAINT document_resources_built_for_stays CHECK (connection <> 'built_for' OR removed_at IS NULL),
    CONSTRAINT document_resources_removed CHECK ((removed_at IS NULL) = (removed_by IS NULL))
);
COMMENT ON TABLE document_resources IS
    'A document''s links to resources: built_for (what it was created for; never removed) or linked; history kept via removed_at.';
CREATE UNIQUE INDEX document_resources_active_environment ON document_resources(document_id, environment_id)
    WHERE removed_at IS NULL AND environment_id IS NOT NULL;
CREATE UNIQUE INDEX document_resources_active_service ON document_resources(document_id, service_id)
    WHERE removed_at IS NULL AND service_id IS NOT NULL;
CREATE UNIQUE INDEX document_resources_active_server ON document_resources(document_id, server_id)
    WHERE removed_at IS NULL AND server_id IS NOT NULL;
CREATE UNIQUE INDEX document_resources_active_code_project ON document_resources(document_id, code_project_id)
    WHERE removed_at IS NULL AND code_project_id IS NOT NULL;
CREATE UNIQUE INDEX document_resources_active_deployment ON document_resources(document_id, deployment_id)
    WHERE removed_at IS NULL AND deployment_id IS NOT NULL;
CREATE INDEX document_resources_environment ON document_resources(environment_id) WHERE removed_at IS NULL;
CREATE INDEX document_resources_service ON document_resources(service_id) WHERE removed_at IS NULL;
CREATE INDEX document_resources_server ON document_resources(server_id) WHERE removed_at IS NULL;
CREATE INDEX document_resources_code_project ON document_resources(code_project_id) WHERE removed_at IS NULL;
CREATE INDEX document_resources_deployment ON document_resources(deployment_id) WHERE removed_at IS NULL;
CREATE INDEX document_resources_document ON document_resources(document_id, created_at);

CREATE FUNCTION document_resources_owner() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM documents WHERE id = NEW.document_id AND draft_of IS NOT NULL) THEN
        RAISE EXCEPTION 'Links belong to the document, not to a draft of it';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER document_resources_owner BEFORE INSERT OR UPDATE OF document_id ON document_resources
    FOR EACH ROW EXECUTE FUNCTION document_resources_owner();

INSERT INTO document_resources(project_id, document_id, environment_id, connection, added_how)
    SELECT project_id, document_id, environment_id, 'linked', 'migrated' FROM document_environments;
INSERT INTO document_resources(project_id, document_id, service_id, connection, added_how)
    SELECT project_id, document_id, service_id, 'linked', 'migrated' FROM document_services;
INSERT INTO document_resources(project_id, document_id, server_id, connection, added_how)
    SELECT project_id, document_id, server_id, 'linked', 'migrated' FROM document_servers;
INSERT INTO document_resources(project_id, document_id, code_project_id, connection, added_how)
    SELECT project_id, document_id, code_project_id, 'linked', 'migrated' FROM document_code_projects;
INSERT INTO document_resources(project_id, document_id, deployment_id, connection, added_how)
    SELECT project_id, document_id, deployment_id, 'linked', 'migrated' FROM document_deployments;

-- A new-document draft's links-to-be (resource_ids JSON) become its built-for links; ids that no longer exist are dropped.
INSERT INTO document_resources(project_id, document_id, environment_id, connection, added_how, added_by, session_id,
    created_at)
SELECT DISTINCT x.project_id, x.id, e.id, 'built_for', CASE WHEN x.session_id IS NULL THEN 'person' ELSE 'agent_run' END,
    x.created_by, x.session_id, x.created_at
FROM document_drafts x
CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(x.resource_ids -> 'environment_ids', '[]')) AS ids(value)
JOIN environments e ON e.id = ids.value::uuid AND e.project_id = x.project_id
WHERE x.status = 'open' AND x.document_id IS NULL;
INSERT INTO document_resources(project_id, document_id, service_id, connection, added_how, added_by, session_id,
    created_at)
SELECT DISTINCT x.project_id, x.id, e.id, 'built_for', CASE WHEN x.session_id IS NULL THEN 'person' ELSE 'agent_run' END,
    x.created_by, x.session_id, x.created_at
FROM document_drafts x
CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(x.resource_ids -> 'service_ids', '[]')) AS ids(value)
JOIN services e ON e.id = ids.value::uuid AND e.project_id = x.project_id
WHERE x.status = 'open' AND x.document_id IS NULL;
INSERT INTO document_resources(project_id, document_id, server_id, connection, added_how, added_by, session_id,
    created_at)
SELECT DISTINCT x.project_id, x.id, e.id, 'built_for', CASE WHEN x.session_id IS NULL THEN 'person' ELSE 'agent_run' END,
    x.created_by, x.session_id, x.created_at
FROM document_drafts x
CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(x.resource_ids -> 'server_ids', '[]')) AS ids(value)
JOIN servers e ON e.id = ids.value::uuid AND e.project_id = x.project_id
WHERE x.status = 'open' AND x.document_id IS NULL;
INSERT INTO document_resources(project_id, document_id, code_project_id, connection, added_how, added_by, session_id,
    created_at)
SELECT DISTINCT x.project_id, x.id, e.id, 'built_for', CASE WHEN x.session_id IS NULL THEN 'person' ELSE 'agent_run' END,
    x.created_by, x.session_id, x.created_at
FROM document_drafts x
CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(x.resource_ids -> 'code_project_ids', '[]')) AS ids(value)
JOIN repositories e ON e.id = ids.value::uuid AND e.project_id = x.project_id
WHERE x.status = 'open' AND x.document_id IS NULL;
INSERT INTO document_resources(project_id, document_id, deployment_id, connection, added_how, added_by, session_id,
    created_at)
SELECT DISTINCT x.project_id, x.id, e.id, 'built_for', CASE WHEN x.session_id IS NULL THEN 'person' ELSE 'agent_run' END,
    x.created_by, x.session_id, x.created_at
FROM document_drafts x
CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(x.resource_ids -> 'deployment_ids', '[]')) AS ids(value)
JOIN service_deployments e ON e.id = ids.value::uuid AND e.project_id = x.project_id
WHERE x.status = 'open' AND x.document_id IS NULL;

-- 5. sources follow their owner.
ALTER TABLE document_sources
    ADD COLUMN document_id uuid REFERENCES documents(id) ON DELETE CASCADE,
    ADD COLUMN archive_id uuid REFERENCES document_archive(id) ON DELETE CASCADE;
DELETE FROM document_sources s USING document_drafts x WHERE s.draft_id = x.id AND x.status <> 'open';
UPDATE document_sources SET document_id = draft_id WHERE draft_id IS NOT NULL;
UPDATE document_sources s SET document_id = d.id FROM documents d
    WHERE s.version_id IS NOT NULL AND d.current_version_id = s.version_id;
UPDATE document_sources SET archive_id = version_id WHERE version_id IS NOT NULL AND document_id IS NULL;
ALTER TABLE document_sources
    DROP CONSTRAINT document_sources_owner,
    DROP COLUMN draft_id, DROP COLUMN version_id, DROP COLUMN source_version_id,
    ADD CONSTRAINT document_sources_owner CHECK ((document_id IS NULL) <> (archive_id IS NULL));
CREATE UNIQUE INDEX document_sources_document ON document_sources(document_id, source_document_id)
    WHERE document_id IS NOT NULL;
CREATE UNIQUE INDEX document_sources_archive ON document_sources(archive_id, source_document_id)
    WHERE archive_id IS NOT NULL;
COMMENT ON TABLE document_sources IS
    'What a document or draft (document_id) or an archived version (archive_id) was built from: parent (the document revised) or read (a document its author read), with the version live then (source_document_id, source_version).';

-- 6. a run remembers its last draft (drafts are deleted on promote and discard).
ALTER TABLE agent_sessions ADD COLUMN last_draft_id uuid;
ALTER TABLE agent_sessions DISABLE TRIGGER touch_updated_at;
UPDATE agent_sessions s SET last_draft_id = (
    SELECT x.id FROM document_drafts x WHERE x.session_id = s.id ORDER BY x.created_at DESC, x.id DESC LIMIT 1)
WHERE EXISTS (SELECT 1 FROM document_drafts x WHERE x.session_id = s.id);
ALTER TABLE agent_sessions ENABLE TRIGGER touch_updated_at;
COMMENT ON COLUMN agent_sessions.last_draft_id IS
    'The draft this run wrote last (no foreign key: promote and discard delete drafts; the audit log has the outcome).';

-- 7. drop the old shape.
DROP VIEW document_details;
ALTER TABLE documents DROP COLUMN current_version_id, DROP COLUMN published_version_id;
DROP TABLE document_drafts;
DROP FUNCTION freeze_decided_document_draft();
DROP TABLE document_versions;
DROP TABLE document_environments, document_services, document_servers, document_code_projects, document_deployments;

-- 8. views.
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

-- Drafts with their document's name and the links that apply (the live document's, or a brand-new draft's own).
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
