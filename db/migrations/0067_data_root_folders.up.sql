SET search_path = ka, public;

-- A collection folder is any visible path under DATA_ROOT; the first segment is no longer one of five areas.
ALTER TABLE rag_collection_paths DROP CONSTRAINT rag_collection_paths_source_path_check;
ALTER TABLE rag_collection_paths ADD CONSTRAINT rag_collection_paths_source_path_check CHECK (
    length(source_path) <= 500 AND source_path ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$');

-- Jira exports move from rag-data/jira to the default JIRA_FOLDER, jira (make setup moves the files).
-- Only that folder and its own files change: rag-data/jira2 or a whole rag-data folder stay as they are.
UPDATE rag_collection_paths SET source_path = substr(source_path, length('rag-data/') + 1)
    WHERE source_path = 'rag-data/jira' OR source_path LIKE 'rag-data/jira/%';
UPDATE rag_files f SET path = substr(f.path, length('rag-data/') + 1) FROM rag_collection_paths p
    WHERE p.id = f.path_id AND (p.source_path = 'jira' OR p.source_path LIKE 'jira/%') AND f.path LIKE 'rag-data/jira/%';
UPDATE rag_file_log l SET path = substr(l.path, length('rag-data/') + 1) FROM rag_collection_paths p
    WHERE p.id = l.path_id AND (p.source_path = 'jira' OR p.source_path LIKE 'jira/%') AND l.path LIKE 'rag-data/jira/%';

-- Saved agent versions hold container paths; DATA_ROOT is now mounted once at /work/data.
UPDATE agent_versions SET config = regexp_replace(regexp_replace(config::text,
        '"/work/rag-data/jira(["/])', '"/work/data/jira\1', 'g'),
        '"/work/(code|code-index|generated-documents|docs)(["/])', '"/work/data/\1\2', 'g')::jsonb
    WHERE config::text ~ '"/work/(rag-data/jira|code|code-index|generated-documents|docs)["/]';
