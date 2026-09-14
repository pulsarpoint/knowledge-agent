BEGIN;
ALTER TABLE ka.llm_models DROP CONSTRAINT llm_models_api_format_check;
ALTER TABLE ka.llm_models DROP CONSTRAINT llm_models_check;
ALTER TABLE ka.llm_models ADD CONSTRAINT llm_models_api_format_check
    CHECK (api_format IN ('chat_completions','responses','native','embeddings'));
ALTER TABLE ka.llm_models ADD CONSTRAINT llm_models_provider_format_check CHECK (
    (provider='openai' AND api_format IN ('chat_completions','responses','embeddings') AND base_url IS NOT NULL AND aws_region IS NULL)
    OR (provider='anthropic' AND api_format='native' AND base_url IS NOT NULL AND aws_region IS NULL)
    OR (provider='bedrock' AND api_format='native' AND base_url IS NULL AND aws_region IS NOT NULL));
ALTER TABLE ka.llm_models ADD COLUMN embedding_dimensions integer;
ALTER TABLE ka.llm_models ADD COLUMN embedding_version text;
ALTER TABLE ka.llm_models ADD CONSTRAINT llm_models_embedding_fields_check CHECK (
    (api_format='embeddings' AND embedding_dimensions IS NOT NULL AND embedding_dimensions BETWEEN 1 AND 16000
        AND embedding_version IS NOT NULL AND length(btrim(embedding_version)) BETWEEN 1 AND 200)
    OR (api_format<>'embeddings' AND embedding_dimensions IS NULL AND embedding_version IS NULL));
ALTER TABLE ka.rag_workspaces ADD COLUMN embedding_model_id uuid REFERENCES ka.llm_models(id);

INSERT INTO ka.principals(issuer,subject,display_name)
SELECT 'knowledge-agent','embedding-model-migration','RAG model migration'
WHERE EXISTS (SELECT 1 FROM ka.rag_workspaces WHERE coalesce(settings->>'embedding_model','')<>'' OR embedding_secret IS NOT NULL)
ON CONFLICT (issuer,subject) DO NOTHING;

-- Reuse the workspace UUID for the migrated model. Embedding key AAD remains
-- <UUID>:embedding, so its ciphertext can move without decrypting credentials.
INSERT INTO ka.llm_models(id,name,provider,model,api_format,base_url,api_key_ciphertext,
    embedding_dimensions,embedding_version,enabled,created_by,updated_by)
SELECT w.project_id,'RAG embeddings ('||w.project_id||')','openai',
    coalesce(nullif(w.settings->>'embedding_model',''),'unconfigured'),'embeddings',
    coalesce(w.settings->>'embedding_base_url','http://litellm:4400/v1'),w.embedding_secret,
    coalesce((w.settings->>'embedding_dimensions')::integer,1024),coalesce(w.settings->>'embedding_version','1'),
    coalesce(w.settings->>'embedding_model','')<>'',p.id,p.id
FROM ka.rag_workspaces w CROSS JOIN ka.principals p
WHERE p.issuer='knowledge-agent' AND p.subject='embedding-model-migration'
    AND (coalesce(w.settings->>'embedding_model','')<>'' OR w.embedding_secret IS NOT NULL);
UPDATE ka.rag_workspaces w SET embedding_model_id=m.id FROM ka.llm_models m
WHERE m.id=w.project_id AND m.api_format='embeddings';
UPDATE ka.rag_workspaces SET settings=settings-ARRAY[
    'embedding_base_url','embedding_model','embedding_version','embedding_dimensions'];
ALTER TABLE ka.rag_workspaces DROP COLUMN embedding_secret;

CREATE FUNCTION ka.embedding_model_updated() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.api_format='embeddings')<>(NEW.api_format='embeddings') THEN
        RAISE EXCEPTION 'Create a separate connection when changing between chat and embeddings';
    END IF;
    UPDATE ka.rag_workspaces SET revision=revision+1,updated_at=now() WHERE embedding_model_id=NEW.id;
    RETURN NEW;
END $$;
CREATE TRIGGER embedding_model_updated AFTER UPDATE ON ka.llm_models
    FOR EACH ROW EXECUTE FUNCTION ka.embedding_model_updated();
COMMENT ON TABLE ka.rag_workspaces IS 'File processing settings and a shared embedding model selection. Source root is deployment-only.';
COMMIT;
