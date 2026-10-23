BEGIN;
SET search_path = ka, public;

-- The embedding model memory uses is a memory setting, one row per project. It starts as the RAG workspace's model, so
-- memory search keeps using the model it used before; RAG goes away in a later migration.
CREATE TABLE memory_settings (
    project_id uuid PRIMARY KEY REFERENCES projects(id),
    embedding_model_id uuid REFERENCES llm_models(id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON memory_settings
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

INSERT INTO memory_settings(project_id, embedding_model_id)
SELECT project_id, embedding_model_id FROM rag_workspaces WHERE embedding_model_id IS NOT NULL;

COMMIT;
