SET search_path = ka, public;

DROP TABLE rag_file_log;
ALTER TABLE rag_collections DROP COLUMN last_polled_at, DROP COLUMN poll_interval_minutes, DROP COLUMN auto_sync;

-- The columns come back empty: no folder has an owner and no code project is searchable.
ALTER TABLE repositories
    ADD COLUMN search_collection text CHECK (search_collection ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(search_collection) <= 60),
    ADD COLUMN search_error text CHECK (length(search_error) <= 2000);
ALTER TABLE rag_collection_paths
    ADD COLUMN owner_kind text CHECK (owner_kind IN ('documents', 'jira', 'code_project')),
    ADD COLUMN owner_id uuid,
    ADD COLUMN owner_key text CHECK (length(owner_key) BETWEEN 1 AND 500),
    ADD CONSTRAINT rag_collection_paths_check CHECK (owner_kind IS NOT NULL OR (owner_id IS NULL AND owner_key IS NULL));
CREATE UNIQUE INDEX rag_collection_paths_owner ON rag_collection_paths(owner_kind, owner_id, owner_key)
    NULLS NOT DISTINCT WHERE owner_kind IS NOT NULL;
COMMENT ON TABLE rag_collection_paths IS 'A folder under DATA_ROOT indexed into a collection, optionally owned by the source that writes it.';
