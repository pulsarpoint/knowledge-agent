SET search_path = ka, public;

ALTER TABLE repositories ADD COLUMN checkout_path relative_path;
CREATE UNIQUE INDEX repositories_active_remote_url
    ON repositories(project_id, remote_url) WHERE archived_at IS NULL;
COMMENT ON COLUMN repositories.checkout_path IS
    'Current complete checkout relative to CODE_ROOT. Published with current_commit after a successful sync; previous checkouts remain on disk.';
