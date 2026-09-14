SET search_path = ka, public;

ALTER TABLE rag_folders
    ADD COLUMN deleted_at timestamptz,
    ADD COLUMN source_cleanup_pending boolean NOT NULL DEFAULT false,
    ADD CONSTRAINT rag_folder_cleanup_requires_deletion CHECK (NOT source_cleanup_pending OR deleted_at IS NOT NULL),
    DROP CONSTRAINT rag_folders_project_id_path_key;

CREATE UNIQUE INDEX rag_folders_active_path ON rag_folders(project_id, path) WHERE deleted_at IS NULL;
CREATE INDEX rag_folder_pending_cleanup ON rag_folders(project_id) WHERE source_cleanup_pending;
COMMENT ON COLUMN rag_folders.deleted_at IS 'Deleted folders retain only identity/path for immutable job history; descriptions, files, chunks, and unreferenced vectors are purged.';
COMMENT ON COLUMN rag_folders.source_cleanup_pending IS 'Durable recursive source removal after index deletion commits; the worker retries interrupted or failed filesystem cleanup.';
