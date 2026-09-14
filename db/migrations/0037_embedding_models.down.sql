BEGIN;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM ka.llm_models WHERE api_format='embeddings') THEN
        RAISE EXCEPTION 'Embedding models exist; rollback would lose their settings and credentials';
    END IF;
END $$;
DROP TRIGGER embedding_model_updated ON ka.llm_models;
DROP FUNCTION ka.embedding_model_updated();
ALTER TABLE ka.rag_workspaces DROP COLUMN embedding_model_id;
ALTER TABLE ka.rag_workspaces ADD COLUMN embedding_secret bytea;
UPDATE ka.rag_workspaces SET settings=settings||'{"embedding_base_url":"http://litellm:4400/v1","embedding_model":"","embedding_version":"1","embedding_dimensions":1024}'::jsonb;
ALTER TABLE ka.llm_models DROP CONSTRAINT llm_models_embedding_fields_check;
ALTER TABLE ka.llm_models DROP COLUMN embedding_dimensions;
ALTER TABLE ka.llm_models DROP COLUMN embedding_version;
ALTER TABLE ka.llm_models DROP CONSTRAINT llm_models_api_format_check;
ALTER TABLE ka.llm_models DROP CONSTRAINT llm_models_provider_format_check;
ALTER TABLE ka.llm_models ADD CONSTRAINT llm_models_api_format_check CHECK (api_format IN ('chat_completions','responses','native'));
ALTER TABLE ka.llm_models ADD CONSTRAINT llm_models_check CHECK (
    (provider='openai' AND api_format IN ('chat_completions','responses') AND base_url IS NOT NULL AND aws_region IS NULL)
    OR (provider='anthropic' AND api_format='native' AND base_url IS NOT NULL AND aws_region IS NULL)
    OR (provider='bedrock' AND api_format='native' AND base_url IS NULL AND aws_region IS NOT NULL));
COMMIT;
