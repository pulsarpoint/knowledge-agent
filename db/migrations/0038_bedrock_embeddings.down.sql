BEGIN;
-- Reject rollback while Bedrock embedding connections are saved; preserve their credentials.
ALTER TABLE ka.llm_models DROP CONSTRAINT llm_models_provider_format_check;
ALTER TABLE ka.llm_models ADD CONSTRAINT llm_models_provider_format_check CHECK (
    (provider='openai' AND api_format IN ('chat_completions','responses','embeddings') AND base_url IS NOT NULL AND aws_region IS NULL)
    OR (provider='anthropic' AND api_format='native' AND base_url IS NOT NULL AND aws_region IS NULL)
    OR (provider='bedrock' AND api_format='native' AND base_url IS NULL AND aws_region IS NOT NULL));
COMMIT;
