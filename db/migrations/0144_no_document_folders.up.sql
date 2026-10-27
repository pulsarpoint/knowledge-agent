BEGIN;
SET search_path = ka, public;

-- Documents lose their folder, a leftover of RAG (nothing is written to disk): a document is named by its slug in the
-- project. Documents are organized by their resource links, tags and search. Slugs that would collide are renamed
-- first: in each project and slug a live document keeps it before a brand-new draft, the oldest first; the others get
-- the first free -2, -3, ... (shortened to stay within 120 characters). A new-document run (agent_sessions purpose
-- documents) loses its folder too.
DO $$
DECLARE
    row record;
    number integer;
    candidate text;
BEGIN
    FOR row IN
        SELECT id, project_id, slug FROM (
            SELECT id, project_id, slug,
                row_number() OVER (PARTITION BY project_id, slug ORDER BY is_draft, created_at, id) AS position
            FROM documents WHERE slug IS NOT NULL) ranked
        WHERE position > 1 ORDER BY project_id, slug, position
    LOOP
        number := 2;
        LOOP
            candidate := rtrim(left(row.slug, 119 - length(number::text)), '-') || '-' || number;
            EXIT WHEN NOT EXISTS (SELECT 1 FROM documents WHERE project_id = row.project_id AND slug = candidate);
            number := number + 1;
        END LOOP;
        UPDATE documents SET slug = candidate WHERE id = row.id;
    END LOOP;
END $$;

DROP VIEW document_drafts_view;
DROP VIEW document_details;
DROP VIEW live_documents;

ALTER TABLE documents DROP CONSTRAINT documents_kind;
DROP INDEX documents_folder_slug;
ALTER TABLE documents DROP COLUMN folder;
ALTER TABLE documents ADD CONSTRAINT documents_kind CHECK (
    (NOT is_draft AND draft_of IS NULL AND slug IS NOT NULL AND version IS NOT NULL
        AND version_created_at IS NOT NULL AND base_version IS NULL)
    OR (is_draft AND version IS NULL AND draft_of IS NULL AND slug IS NOT NULL AND base_version IS NULL)
    OR (is_draft AND version IS NULL AND draft_of IS NOT NULL AND slug IS NULL));
-- Live documents and brand-new drafts reserve their slug in the project, as (project, folder, slug) did.
CREATE UNIQUE INDEX documents_slug ON documents(project_id, slug) WHERE slug IS NOT NULL;

CREATE VIEW live_documents AS SELECT * FROM documents WHERE NOT is_draft;

CREATE VIEW document_details AS
SELECT d.id, d.project_id, d.slug, d.revision, d.created_at, d.updated_at, d.phase_metadata,
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
    x.title, x.description, x.purpose, x.audience, x.tags, x.markdown,
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

ALTER TABLE agent_sessions DROP CONSTRAINT agent_sessions_documentation;
ALTER TABLE agent_sessions DROP COLUMN folder;
ALTER TABLE agent_sessions ADD CONSTRAINT agent_sessions_documentation CHECK (
    CASE purpose
        WHEN 'documents' THEN document_name IS NOT NULL
        ELSE document_name IS NULL AND draft IS NULL AND draft_turn IS NULL AND document_id IS NULL
    END);
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a new-document run (document name, draft); event: started by an event handler (Send to agent); resource: a chat on a server, service or environment (its tag says which); revision: an Ask agent run on a document; chat: a saved agent''s conversation (its Chat tab, Run agent).';

COMMIT;
