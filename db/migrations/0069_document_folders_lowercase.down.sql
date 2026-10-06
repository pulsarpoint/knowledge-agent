SET search_path = ka, public;

ALTER TABLE documents DROP CONSTRAINT documents_folder_check;
ALTER TABLE documents ADD CONSTRAINT documents_folder_check CHECK (
    length(folder) <= 200 AND folder ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$'
    AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services');
