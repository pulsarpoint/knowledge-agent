SET search_path = ka, public;

DO $$
DECLARE crowded text;
BEGIN
    SELECT c.name INTO crowded FROM rag_collections c JOIN rag_collection_paths p ON p.collection_id = c.id
    WHERE c.deleted_at IS NULL GROUP BY c.id, c.name HAVING count(*) > 1 ORDER BY c.name LIMIT 1;
    IF crowded IS NOT NULL THEN
        RAISE EXCEPTION 'Collection % has more than one path; remove the extra paths before rolling back 0065', crowded;
    END IF;
END $$;

ALTER TABLE repositories DROP COLUMN search_error, DROP COLUMN search_collection;

-- A collection without a path gets the rag-data folder the old create API would have used.
ALTER TABLE rag_collections ADD COLUMN source_path text;
UPDATE rag_collections c SET source_path = coalesce(
    (SELECT p.source_path FROM rag_collection_paths p WHERE p.collection_id = c.id ORDER BY p.created_at, p.source_path LIMIT 1),
    'rag-data/' || c.name);
ALTER TABLE rag_collections ALTER COLUMN source_path SET NOT NULL,
    ADD CONSTRAINT rag_collections_source_path_check CHECK (length(source_path) <= 500 AND source_path ~
        '^(code|code-index|rag-data|generated-documents|docs)(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$');

-- Only deleted collections can still have files under a second path; those rows cannot be represented.
DELETE FROM rag_files f USING rag_collections c, rag_collection_paths p
WHERE c.id = f.collection_id AND p.id = f.path_id AND p.source_path <> c.source_path;
UPDATE rag_files f SET path = substr(f.path, length(p.source_path) + 2)
FROM rag_collection_paths p WHERE p.id = f.path_id;
ALTER TABLE rag_files DROP COLUMN path_id;
DROP TABLE rag_collection_paths;
COMMENT ON TABLE rag_collections IS 'Searchable unit: one folder under DATA_ROOT indexed in place with its own settings.';
