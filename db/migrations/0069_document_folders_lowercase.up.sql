SET search_path = ka, public;

-- Documentation folders are lowercase-only: on case-insensitive filesystems (macOS) Payments and payments are the
-- same directory, so two folders differing only in case would share their exports.
DO $$
DECLARE
    upper_folder text;
BEGIN
    SELECT folder INTO upper_folder FROM documents WHERE folder <> lower(folder) ORDER BY folder LIMIT 1;
    IF upper_folder IS NOT NULL THEN
        RAISE EXCEPTION 'Documentation folder % has uppercase letters; folders must be lowercase. Move its documents to a lowercase folder before applying 0069', upper_folder;
    END IF;
END $$;

ALTER TABLE documents DROP CONSTRAINT documents_folder_check;
ALTER TABLE documents ADD CONSTRAINT documents_folder_check CHECK (
    length(folder) <= 200 AND folder ~ '^[a-z0-9_-][a-z0-9._-]*(/[a-z0-9_-][a-z0-9._-]*)*$'
    AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services');
