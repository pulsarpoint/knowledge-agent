SET search_path = ka, public;

CREATE TABLE llm_models (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE CHECK (char_length(btrim(name)) BETWEEN 1 AND 100),
    provider text NOT NULL CHECK (provider IN ('openai','anthropic','bedrock')),
    model text NOT NULL CHECK (char_length(btrim(model)) BETWEEN 1 AND 500),
    api_format text NOT NULL CHECK (api_format IN ('chat_completions','responses','native')),
    base_url text,
    aws_region text,
    api_key_ciphertext bytea,
    enabled boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid NOT NULL REFERENCES principals(id),
    updated_by uuid NOT NULL REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((provider='openai' AND api_format IN ('chat_completions','responses') AND base_url IS NOT NULL AND aws_region IS NULL)
        OR (provider='anthropic' AND api_format='native' AND base_url IS NOT NULL AND aws_region IS NULL)
        OR (provider='bedrock' AND api_format='native' AND base_url IS NULL AND aws_region IS NOT NULL)),
    CHECK (provider='openai' OR api_key_ciphertext IS NOT NULL)
);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON llm_models
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE llm_assignments (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    search_model_id uuid REFERENCES llm_models(id),
    code_model_id uuid REFERENCES llm_models(id),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO llm_assignments(singleton) VALUES (true);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON llm_assignments
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMENT ON TABLE llm_models IS 'Named model connections for the single application; encrypted credentials are never included in public projections.';
COMMENT ON TABLE llm_assignments IS 'Agent model selections, read afresh for each request.';
