SET search_path = ka, public;

-- A collection indexes several folders; each may belong to the source that writes it.
CREATE TABLE rag_collection_paths (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id uuid NOT NULL REFERENCES rag_collections(id) ON DELETE CASCADE,
    source_path text NOT NULL CHECK (length(source_path) <= 500 AND source_path ~
        '^(code|code-index|rag-data|generated-documents|docs)(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$'),
    owner_kind text CHECK (owner_kind IN ('documents', 'jira', 'code_project')),
    owner_id uuid,
    owner_key text CHECK (length(owner_key) BETWEEN 1 AND 500),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (owner_kind IS NOT NULL OR (owner_id IS NULL AND owner_key IS NULL)),
    UNIQUE (collection_id, source_path),
    UNIQUE (id, collection_id)
);
-- A source owns at most one path; adding it again re-points it. The RAG workspace is single-project.
CREATE UNIQUE INDEX rag_collection_paths_owner ON rag_collection_paths(owner_kind, owner_id, owner_key)
    NULLS NOT DISTINCT WHERE owner_kind IS NOT NULL;
COMMENT ON TABLE rag_collection_paths IS 'A folder under DATA_ROOT indexed into a collection, optionally owned by the source that writes it.';

-- Each collection's folder becomes its single path. Documents and Jira folders get their
-- owner; when several live collections share such a folder, the oldest one keeps the owner.
INSERT INTO rag_collection_paths(collection_id, source_path, owner_kind, created_at)
SELECT c.id, c.source_path,
       CASE WHEN c.deleted_at IS NOT NULL OR c.rank > 1 THEN NULL
            WHEN c.source_path = 'generated-documents' THEN 'documents'
            WHEN c.source_path = 'rag-data/jira' THEN 'jira' END,
       c.created_at
FROM (SELECT *, row_number() OVER (PARTITION BY source_path, deleted_at IS NULL ORDER BY created_at, id) AS rank
      FROM rag_collections) c;

-- Indexed files keep their chunks and vectors; their paths become full DATA_ROOT paths.
ALTER TABLE rag_files ADD COLUMN path_id uuid;
UPDATE rag_files f SET path_id = p.id, path = p.source_path || '/' || f.path
FROM rag_collection_paths p WHERE p.collection_id = f.collection_id;
ALTER TABLE rag_files ALTER COLUMN path_id SET NOT NULL,
    ADD CONSTRAINT rag_files_path_fk FOREIGN KEY (path_id, collection_id)
        REFERENCES rag_collection_paths(id, collection_id) ON DELETE CASCADE;
CREATE INDEX rag_files_path ON rag_files(path_id);

ALTER TABLE rag_collections DROP COLUMN source_path;
COMMENT ON TABLE rag_collections IS 'Searchable unit with its own settings; its folders are listed in rag_collection_paths.';

-- Code projects remember whether they are searchable, in which collection, and the last RAG error.
ALTER TABLE repositories
    ADD COLUMN search_collection text CHECK (search_collection ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(search_collection) <= 60),
    ADD COLUMN search_error text CHECK (length(search_error) <= 2000);
