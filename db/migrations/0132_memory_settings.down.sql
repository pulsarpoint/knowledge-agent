BEGIN;
SET search_path = ka, public;

-- Back to 0131: the setting is copied to the RAG workspace (which still holds a column for it), then the table goes.
UPDATE rag_workspaces w SET embedding_model_id = s.embedding_model_id
FROM memory_settings s WHERE s.project_id = w.project_id AND s.embedding_model_id IS NOT NULL;
DROP TABLE memory_settings;

COMMIT;
