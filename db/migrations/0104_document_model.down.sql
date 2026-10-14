BEGIN;
SET search_path = ka, public;
-- documents' deferred version foreign keys are checked per statement, so ALTER TABLE never meets pending checks.
SET CONSTRAINTS ALL IMMEDIATE;

-- Back to 0103: versions in document_versions, drafts in document_drafts, links in the five link tables.

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM document_resources r JOIN documents d ON d.id = r.document_id
               WHERE NOT d.is_draft AND NOT (r.connection = 'linked' AND r.added_how = 'migrated' AND r.removed_at IS NULL)) THEN
        RAISE EXCEPTION 'Document link provenance or history exists and would be lost; rollback is not possible after links were added, removed or built for a document';
    END IF;
END $$;

DROP VIEW document_drafts_view;
DROP VIEW document_details;
DROP VIEW live_documents;

-- 1. versions: the archive (same ids) and one version per live document.
CREATE TABLE document_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    title nonblank NOT NULL CHECK (length(title) <= 240),
    description text NOT NULL CHECK (length(description) <= 4000),
    purpose nonblank NOT NULL CHECK (length(purpose) <= 80),
    audience text NOT NULL CHECK (audience IN ('people','agents','both')),
    tags text[] NOT NULL DEFAULT '{}',
    markdown nonblank NOT NULL CHECK (length(markdown) <= 1000000),
    content_hash sha256 NOT NULL CHECK (content_hash=encode(sha256(convert_to(markdown,'UTF8')),'hex')),
    source_snapshot jsonb NOT NULL CHECK (jsonb_typeof(source_snapshot)='object'),
    base_version_id uuid,
    origin text NOT NULL CHECK (origin IN ('manual','generated','uploaded')),
    generation_id uuid,
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_versions_generation_origin CHECK (generation_id IS NULL OR origin = 'generated'),
    FOREIGN KEY (document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    UNIQUE (id,document_id),
    UNIQUE (document_id,version),
    FOREIGN KEY (base_version_id,document_id) REFERENCES document_versions(id,document_id)
);
CREATE INDEX document_versions_generation ON document_versions(generation_id) WHERE generation_id IS NOT NULL;
CREATE TRIGGER reject_mutation BEFORE UPDATE ON document_versions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
COMMENT ON COLUMN document_versions.source_snapshot IS 'Names and inventory references at save time, retained even if linked inventory is removed. Never store credentials.';
COMMENT ON COLUMN document_versions.generation_id IS 'The removed one-shot generation run that wrote this version (history only since 0073).';

INSERT INTO document_versions(id, document_id, project_id, version, title, description, purpose, audience, tags,
    markdown, content_hash, source_snapshot, origin, created_by, created_at)
SELECT a.id, a.document_id, a.project_id, a.version, a.title, a.description, a.purpose, a.audience, a.tags,
    a.markdown, a.content_hash, a.source_snapshot, a.origin, a.created_by, a.created_at
FROM document_archive a;
INSERT INTO document_versions(document_id, project_id, version, title, description, purpose, audience, tags,
    markdown, content_hash, source_snapshot, origin, created_by, created_at)
SELECT d.id, d.project_id, d.version, d.title, d.description, d.purpose, d.audience, d.tags,
    d.markdown, d.content_hash, d.source_snapshot, d.origin, d.version_created_by, d.version_created_at
FROM documents d WHERE NOT d.is_draft;

-- 2. documents point at their current version again (filled before drafts go, NOT NULL after).
ALTER TABLE documents ADD COLUMN current_version_id uuid, ADD COLUMN published_version_id uuid;
ALTER TABLE documents DISABLE TRIGGER touch_updated_at;
UPDATE documents d SET current_version_id = v.id
FROM document_versions v WHERE NOT d.is_draft AND v.document_id = d.id AND v.version = d.version;
ALTER TABLE documents ENABLE TRIGGER touch_updated_at;
ALTER TABLE documents
    ADD CONSTRAINT documents_current_version_fk FOREIGN KEY (current_version_id,id)
        REFERENCES document_versions(id,document_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT documents_published_version_fk FOREIGN KEY (published_version_id,id)
        REFERENCES document_versions(id,document_id) DEFERRABLE INITIALLY DEFERRED;

-- 3. drafts: every draft row becomes an open draft (same id).
CREATE TABLE document_drafts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    document_id uuid,
    slug text CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) <= 120),
    folder text CHECK (length(folder) <= 200 AND folder ~ '^[a-z0-9_-][a-z0-9._-]*(/[a-z0-9_-][a-z0-9._-]*)*$'
        AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services'),
    resource_ids jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(resource_ids) = 'object'),
    base_version_id uuid REFERENCES document_versions(id) ON DELETE SET NULL,
    title nonblank NOT NULL CHECK (length(title) <= 240),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
    purpose nonblank NOT NULL DEFAULT 'explanation' CHECK (length(purpose) <= 80),
    audience text NOT NULL DEFAULT 'both' CHECK (audience IN ('people', 'agents', 'both')),
    tags text[] NOT NULL DEFAULT '{}' CHECK (cardinality(tags) <= 30),
    markdown nonblank NOT NULL CHECK (length(markdown) <= 1000000),
    origin text NOT NULL CHECK (origin IN ('manual', 'agent')),
    session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL,
    edited boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'promoted', 'discarded')),
    promoted_version_id uuid REFERENCES document_versions(id) ON DELETE CASCADE,
    created_by uuid REFERENCES principals(id),
    updated_by uuid REFERENCES principals(id),
    decided_by uuid REFERENCES principals(id),
    decided_at timestamptz,
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE,
    CONSTRAINT document_drafts_target CHECK ((document_id IS NULL) = (slug IS NOT NULL) AND (document_id IS NULL OR folder IS NULL)),
    CONSTRAINT document_drafts_promoted CHECK ((status = 'promoted') = (promoted_version_id IS NOT NULL)),
    CONSTRAINT document_drafts_decided CHECK ((status = 'open') = (decided_at IS NULL))
);
CREATE INDEX document_drafts_open ON document_drafts(document_id) WHERE status = 'open';
CREATE INDEX document_drafts_new ON document_drafts(project_id) WHERE status = 'open' AND document_id IS NULL;
CREATE INDEX document_drafts_session ON document_drafts(session_id) WHERE session_id IS NOT NULL;
CREATE INDEX document_drafts_promoted_version ON document_drafts(promoted_version_id) WHERE promoted_version_id IS NOT NULL;
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON document_drafts
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE document_drafts IS
    'Unpublished versions of a document (document_id, base_version_id: the live version it started from) or of a new document (slug, folder, resource_ids to link on promote). origin agent: written by a Generate documents run (session_id); edited: a person saved it. Promoted (promoted_version_id) or discarded drafts are kept and frozen.';

-- A decided draft never changes; only the ON DELETE SET NULL of its run and base version (and updated_at) may.
CREATE FUNCTION freeze_decided_document_draft() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'open' AND (NEW.status, NEW.document_id, NEW.slug, NEW.folder, NEW.resource_ids, NEW.title,
            NEW.description, NEW.purpose, NEW.audience, NEW.tags, NEW.markdown, NEW.origin, NEW.edited,
            NEW.promoted_version_id, NEW.revision, NEW.decided_by, NEW.decided_at)
        IS DISTINCT FROM (OLD.status, OLD.document_id, OLD.slug, OLD.folder, OLD.resource_ids, OLD.title,
            OLD.description, OLD.purpose, OLD.audience, OLD.tags, OLD.markdown, OLD.origin, OLD.edited,
            OLD.promoted_version_id, OLD.revision, OLD.decided_by, OLD.decided_at) THEN
        RAISE EXCEPTION 'A promoted or discarded draft cannot change' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER freeze_decided_document_draft BEFORE UPDATE ON document_drafts
    FOR EACH ROW EXECUTE FUNCTION freeze_decided_document_draft();

-- A brand-new draft's active links become its resource_ids again (all five keys, as the drafts API writes them).
INSERT INTO document_drafts(id, project_id, document_id, slug, folder, resource_ids, base_version_id, title,
    description, purpose, audience, tags, markdown, origin, session_id, edited, status, created_by, updated_by,
    revision, created_at, updated_at)
SELECT x.id, x.project_id, x.draft_of, x.slug, x.folder,
    CASE WHEN x.draft_of IS NOT NULL THEN '{}'::jsonb ELSE jsonb_build_object(
        'code_project_ids', COALESCE((SELECT jsonb_agg(r.code_project_id ORDER BY r.created_at, r.id) FROM document_resources r
            WHERE r.document_id = x.id AND r.removed_at IS NULL AND r.code_project_id IS NOT NULL), '[]'),
        'service_ids', COALESCE((SELECT jsonb_agg(r.service_id ORDER BY r.created_at, r.id) FROM document_resources r
            WHERE r.document_id = x.id AND r.removed_at IS NULL AND r.service_id IS NOT NULL), '[]'),
        'server_ids', COALESCE((SELECT jsonb_agg(r.server_id ORDER BY r.created_at, r.id) FROM document_resources r
            WHERE r.document_id = x.id AND r.removed_at IS NULL AND r.server_id IS NOT NULL), '[]'),
        'deployment_ids', COALESCE((SELECT jsonb_agg(r.deployment_id ORDER BY r.created_at, r.id) FROM document_resources r
            WHERE r.document_id = x.id AND r.removed_at IS NULL AND r.deployment_id IS NOT NULL), '[]'),
        'environment_ids', COALESCE((SELECT jsonb_agg(r.environment_id ORDER BY r.created_at, r.id) FROM document_resources r
            WHERE r.document_id = x.id AND r.removed_at IS NULL AND r.environment_id IS NOT NULL), '[]')) END,
    (SELECT v.id FROM document_versions v WHERE v.document_id = x.draft_of AND v.version = x.base_version),
    x.title, x.description, x.purpose, x.audience, x.tags, x.markdown,
    CASE WHEN x.origin = 'generated' THEN 'agent' ELSE 'manual' END, x.session_id, x.edited, 'open',
    x.created_by, x.updated_by, x.revision, x.created_at, x.updated_at
FROM documents x WHERE x.is_draft;

-- 4. sources: rebuilt in the 0098 shape (draft_id | version_id owner; source_version_id from the source's number).
CREATE TEMPORARY TABLE document_sources_0104 ON COMMIT DROP AS SELECT * FROM document_sources;
DROP TABLE document_sources;
CREATE TABLE document_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    draft_id uuid REFERENCES document_drafts(id) ON DELETE CASCADE,
    version_id uuid REFERENCES document_versions(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('parent', 'read')),
    source_document_id uuid REFERENCES documents(id) ON DELETE SET NULL,
    source_version_id uuid REFERENCES document_versions(id) ON DELETE SET NULL,
    source_title text NOT NULL CHECK (length(source_title) <= 240),
    source_version integer CHECK (source_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_sources_owner CHECK ((draft_id IS NULL) <> (version_id IS NULL))
);
CREATE UNIQUE INDEX document_sources_draft ON document_sources(draft_id, source_document_id) WHERE draft_id IS NOT NULL;
CREATE UNIQUE INDEX document_sources_version ON document_sources(version_id, source_document_id) WHERE version_id IS NOT NULL;
CREATE INDEX document_sources_source ON document_sources(source_document_id) WHERE source_document_id IS NOT NULL;
COMMENT ON TABLE document_sources IS
    'What a draft (draft_id) or live version (version_id) was built from: parent (the document revised) or read (a document its author read), with the version live then. Copied from a draft to its version on promote.';

INSERT INTO document_sources(id, project_id, draft_id, version_id, kind, source_document_id, source_version_id,
    source_title, source_version, created_at)
SELECT s.id, s.project_id,
    CASE WHEN d.is_draft THEN d.id END,
    CASE WHEN s.archive_id IS NOT NULL THEN s.archive_id WHEN NOT d.is_draft THEN d.current_version_id END,
    s.kind, s.source_document_id,
    (SELECT v.id FROM document_versions v WHERE v.document_id = s.source_document_id AND v.version = s.source_version),
    s.source_title, s.source_version, s.created_at
FROM document_sources_0104 s LEFT JOIN documents d ON d.id = s.document_id;

-- 5. the five link tables, from the active links of live documents.
CREATE TABLE document_code_projects (
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    code_project_id uuid NOT NULL,
    PRIMARY KEY (document_id,code_project_id),
    FOREIGN KEY (document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (code_project_id,project_id) REFERENCES repositories(id,project_id) ON DELETE CASCADE
);
CREATE INDEX document_code_projects_source ON document_code_projects(code_project_id);
CREATE TABLE document_services (
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    service_id uuid NOT NULL,
    PRIMARY KEY (document_id,service_id),
    FOREIGN KEY (document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (service_id,project_id) REFERENCES services(id,project_id) ON DELETE CASCADE
);
CREATE INDEX document_services_source ON document_services(service_id);
CREATE TABLE document_servers (
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    server_id uuid NOT NULL,
    PRIMARY KEY (document_id,server_id),
    FOREIGN KEY (document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id,project_id) REFERENCES servers(id,project_id) ON DELETE CASCADE
);
CREATE INDEX document_servers_source ON document_servers(server_id);
CREATE TABLE document_deployments (
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    deployment_id uuid NOT NULL,
    PRIMARY KEY (document_id,deployment_id),
    FOREIGN KEY (document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (deployment_id,project_id) REFERENCES service_deployments(id,project_id) ON DELETE CASCADE
);
CREATE INDEX document_deployments_source ON document_deployments(deployment_id);
CREATE TABLE document_environments (
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    environment_id uuid NOT NULL,
    PRIMARY KEY(document_id,environment_id),
    FOREIGN KEY(document_id,project_id) REFERENCES documents(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY(environment_id,project_id) REFERENCES environments(id,project_id) ON DELETE CASCADE
);
CREATE INDEX document_environments_source ON document_environments(environment_id);

INSERT INTO document_code_projects(document_id, project_id, code_project_id)
    SELECT r.document_id, r.project_id, r.code_project_id FROM document_resources r JOIN documents d ON d.id = r.document_id
    WHERE NOT d.is_draft AND r.removed_at IS NULL AND r.code_project_id IS NOT NULL;
INSERT INTO document_services(document_id, project_id, service_id)
    SELECT r.document_id, r.project_id, r.service_id FROM document_resources r JOIN documents d ON d.id = r.document_id
    WHERE NOT d.is_draft AND r.removed_at IS NULL AND r.service_id IS NOT NULL;
INSERT INTO document_servers(document_id, project_id, server_id)
    SELECT r.document_id, r.project_id, r.server_id FROM document_resources r JOIN documents d ON d.id = r.document_id
    WHERE NOT d.is_draft AND r.removed_at IS NULL AND r.server_id IS NOT NULL;
INSERT INTO document_deployments(document_id, project_id, deployment_id)
    SELECT r.document_id, r.project_id, r.deployment_id FROM document_resources r JOIN documents d ON d.id = r.document_id
    WHERE NOT d.is_draft AND r.removed_at IS NULL AND r.deployment_id IS NOT NULL;
INSERT INTO document_environments(document_id, project_id, environment_id)
    SELECT r.document_id, r.project_id, r.environment_id FROM document_resources r JOIN documents d ON d.id = r.document_id
    WHERE NOT d.is_draft AND r.removed_at IS NULL AND r.environment_id IS NOT NULL;

-- 6. drop the new shape.
DROP TRIGGER document_links_live ON document_links;
DROP TRIGGER inventory_documents_live ON inventory_documents;
DROP TRIGGER agent_sessions_live_document ON agent_sessions;
DROP TRIGGER discovery_proposals_live_document ON discovery_proposals;
DROP FUNCTION refuse_draft_document();
DROP TABLE document_resources;
DROP FUNCTION document_resources_owner();
DROP TABLE document_archive;
ALTER TABLE agent_sessions DROP COLUMN last_draft_id;

DELETE FROM documents WHERE is_draft;
DROP TRIGGER document_draft_of_live ON documents;
DROP TRIGGER document_stays_live ON documents;
DROP FUNCTION document_draft_of_live();
DROP FUNCTION document_stays_live();
DROP INDEX documents_folder_slug;
ALTER TABLE documents
    DROP CONSTRAINT documents_kind, DROP CONSTRAINT documents_draft_of_fk,
    DROP COLUMN is_draft, DROP COLUMN draft_of, DROP COLUMN version, DROP COLUMN base_version,
    DROP COLUMN title, DROP COLUMN description, DROP COLUMN purpose, DROP COLUMN audience, DROP COLUMN tags,
    DROP COLUMN markdown, DROP COLUMN content_hash, DROP COLUMN source_snapshot, DROP COLUMN origin,
    DROP COLUMN session_id, DROP COLUMN edited, DROP COLUMN from_draft, DROP COLUMN version_created_at,
    DROP COLUMN version_created_by, DROP COLUMN created_by, DROP COLUMN updated_by,
    ALTER COLUMN slug SET NOT NULL,
    ALTER COLUMN current_version_id SET NOT NULL,
    ADD CONSTRAINT documents_project_id_folder_slug_key UNIQUE NULLS NOT DISTINCT (project_id, folder, slug);
COMMENT ON TABLE documents IS 'Managed documents. Canonical Markdown is in immutable document_versions; RAG publishing is a separate operation.';

-- 7. the 0068 view, with d.* written out as 0068 expanded it (phase_metadata came later, in 0073).
CREATE VIEW document_details AS
SELECT d.id, d.project_id, d.slug, d.current_version_id, d.published_version_id, d.revision, d.created_at,
    d.updated_at, d.folder, v.version, v.title, v.description, v.purpose, v.audience, v.tags, v.content_hash,
    COALESCE((SELECT array_agg(code_project_id ORDER BY code_project_id) FROM document_code_projects WHERE document_id=d.id),'{}') AS code_project_ids,
    COALESCE((SELECT array_agg(service_id ORDER BY service_id) FROM document_services WHERE document_id=d.id),'{}') AS service_ids,
    COALESCE((SELECT array_agg(server_id ORDER BY server_id) FROM document_servers WHERE document_id=d.id),'{}') AS server_ids,
    COALESCE((SELECT array_agg(deployment_id ORDER BY deployment_id) FROM document_deployments WHERE document_id=d.id),'{}') AS deployment_ids,
    COALESCE((SELECT array_agg(environment_id ORDER BY environment_id) FROM document_environments WHERE document_id=d.id),'{}') AS environment_ids
FROM documents d JOIN document_versions v ON v.id=d.current_version_id;

COMMIT;
