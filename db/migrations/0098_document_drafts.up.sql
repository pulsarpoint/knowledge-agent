BEGIN;
SET search_path = ka, public;

-- Drafts: unpublished versions of a document (document_id) or of a new document (slug, folder), promoted to live or
-- discarded by a person. Kept apart from document_versions, so version history, document_details, exports and folder
-- statistics see live versions only.
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

-- What a draft or a live version was built from: the document it revises (parent) and the documents its author read,
-- with a snapshot of title and version so a deleted source still shows "(deleted)".
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

-- Dependencies people add between documents.
CREATE TABLE document_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    from_document_id uuid NOT NULL,
    to_document_id uuid NOT NULL,
    kind text NOT NULL DEFAULT 'depends_on' CHECK (kind IN ('depends_on')),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (from_document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE,
    UNIQUE (from_document_id, to_document_id, kind),
    CONSTRAINT document_links_not_self CHECK (from_document_id <> to_document_id)
);
CREATE INDEX document_links_to ON document_links(to_document_id);
COMMENT ON TABLE document_links IS 'A document depends on another (added by a person on the Connections tab).';

COMMIT;
