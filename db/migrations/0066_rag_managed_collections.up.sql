SET search_path = ka, public;

-- RAG owns the configuration: folders have no owners, and code projects no search settings.
DROP INDEX rag_collection_paths_owner;
ALTER TABLE rag_collection_paths DROP COLUMN owner_kind, DROP COLUMN owner_id, DROP COLUMN owner_key;
COMMENT ON TABLE rag_collection_paths IS 'A folder under DATA_ROOT connected to a collection in RAG; sources only write files into it.';
ALTER TABLE repositories DROP COLUMN search_collection, DROP COLUMN search_error;

-- Automatic sync per collection.
ALTER TABLE rag_collections
    ADD COLUMN auto_sync boolean NOT NULL DEFAULT false,
    ADD COLUMN poll_interval_minutes integer NOT NULL DEFAULT 5 CHECK (poll_interval_minutes BETWEEN 1 AND 1440),
    ADD COLUMN last_polled_at timestamptz;

-- What the last published sync decided for every file it found; rag_files and rag_chunks stay the index.
CREATE TABLE rag_file_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id uuid NOT NULL,
    path_id uuid NOT NULL,
    path text NOT NULL,
    size bigint CHECK (size >= 0),
    mtime double precision,
    content_hash text CHECK (length(content_hash) = 64),
    fingerprint text CHECK (length(fingerprint) = 64),
    status text NOT NULL CHECK (status IN ('indexed', 'skipped_binary', 'skipped_too_large', 'skipped_unreadable', 'failed', 'removed')),
    error text CHECK (length(error) <= 2000),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    indexed_at timestamptz,
    removed_at timestamptz,
    UNIQUE (collection_id, path),
    FOREIGN KEY (path_id, collection_id) REFERENCES rag_collection_paths(id, collection_id) ON DELETE CASCADE
);
CREATE INDEX rag_file_log_status ON rag_file_log(collection_id, status, path);
CREATE INDEX rag_file_log_path ON rag_file_log(path_id);
COMMENT ON TABLE rag_file_log IS 'Per-file sync history of a collection: size, mtime, content hash, processing fingerprint and status.';

-- Indexed files start as indexed. Size, mtime and fingerprint are unknown, so the first sync after the
-- upgrade reads each file once; unchanged content is not parsed or embedded again.
INSERT INTO rag_file_log(collection_id, path_id, path, content_hash, status, first_seen_at, last_seen_at, indexed_at)
SELECT collection_id, path_id, path, content_hash, 'indexed', indexed_at, indexed_at, indexed_at FROM rag_files;
