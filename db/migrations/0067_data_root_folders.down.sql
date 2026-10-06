SET search_path = ka, public;

UPDATE agent_versions SET config = regexp_replace(regexp_replace(config::text,
        '"/work/data/jira(["/])', '"/work/rag-data/jira\1', 'g'),
        '"/work/data/(code|code-index|generated-documents|docs)(["/])', '"/work/\1\2', 'g')::jsonb
    WHERE config::text ~ '"/work/data/(jira|code|code-index|generated-documents|docs)["/]';

UPDATE rag_files f SET path = 'rag-data/' || f.path FROM rag_collection_paths p
    WHERE p.id = f.path_id AND (p.source_path = 'jira' OR p.source_path LIKE 'jira/%') AND f.path LIKE 'jira/%';
UPDATE rag_file_log l SET path = 'rag-data/' || l.path FROM rag_collection_paths p
    WHERE p.id = l.path_id AND (p.source_path = 'jira' OR p.source_path LIKE 'jira/%') AND l.path LIKE 'jira/%';
UPDATE rag_collection_paths SET source_path = 'rag-data/' || source_path
    WHERE source_path = 'jira' OR source_path LIKE 'jira/%';

-- Folders added outside the five areas since the upgrade are kept; only new ones are refused again.
ALTER TABLE rag_collection_paths DROP CONSTRAINT rag_collection_paths_source_path_check;
ALTER TABLE rag_collection_paths ADD CONSTRAINT rag_collection_paths_source_path_check CHECK (
    length(source_path) <= 500 AND source_path ~
    '^(code|code-index|rag-data|generated-documents|docs)(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$') NOT VALID;
