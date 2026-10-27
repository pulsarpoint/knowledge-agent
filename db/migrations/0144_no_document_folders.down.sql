BEGIN;
SET search_path = ka, public;

-- Back to 0143: the folder columns (at the end of their tables), their checks, the (project, folder, slug) index and
-- the views with folder. Every document comes back without a folder; renamed slugs keep their new names. A
-- new-document run needed a folder: existing ones get 'documents' (test data).
DROP VIEW document_drafts_view;
DROP VIEW document_details;
DROP VIEW live_documents;

DROP INDEX documents_slug;
ALTER TABLE documents DROP CONSTRAINT documents_kind;
ALTER TABLE documents ADD COLUMN folder text CONSTRAINT documents_folder_check CHECK (
    length(folder) <= 200 AND folder ~ '^[a-z0-9_-][a-z0-9._-]*(/[a-z0-9_-][a-z0-9._-]*)*$'
    AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services');
ALTER TABLE documents ADD CONSTRAINT documents_kind CHECK (
    (NOT is_draft AND draft_of IS NULL AND slug IS NOT NULL AND version IS NOT NULL
        AND version_created_at IS NOT NULL AND base_version IS NULL)
    OR (is_draft AND version IS NULL AND draft_of IS NULL AND slug IS NOT NULL AND base_version IS NULL)
    OR (is_draft AND version IS NULL AND draft_of IS NOT NULL AND slug IS NULL AND folder IS NULL));
CREATE UNIQUE INDEX documents_folder_slug ON documents(project_id, folder, slug) NULLS NOT DISTINCT
    WHERE slug IS NOT NULL;

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

ALTER TABLE agent_sessions DROP CONSTRAINT agent_sessions_documentation;
ALTER TABLE agent_sessions ADD COLUMN folder text CONSTRAINT agent_sessions_folder_check CHECK (
    length(folder) <= 200 AND folder ~ '^[a-z0-9_-][a-z0-9._-]*(/[a-z0-9_-][a-z0-9._-]*)*$'
    AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services');
UPDATE agent_sessions SET folder = 'documents' WHERE purpose = 'documents';
ALTER TABLE agent_sessions ADD CONSTRAINT agent_sessions_documentation CHECK (
    CASE purpose
        WHEN 'documents' THEN document_name IS NOT NULL AND folder IS NOT NULL
        ELSE document_name IS NULL AND folder IS NULL AND draft IS NULL AND draft_turn IS NULL AND document_id IS NULL
    END);
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a new-document run (document name, folder, draft); event: started by an event handler (Send to agent); resource: a chat on a server, service or environment (its tag says which); revision: an Ask agent run on a document; chat: a saved agent''s conversation (its Chat tab, Run agent).';

COMMIT;
