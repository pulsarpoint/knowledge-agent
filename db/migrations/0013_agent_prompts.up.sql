SET search_path = ka, public;

CREATE TABLE agent_prompts (
    agent text PRIMARY KEY CHECK (agent IN ('search', 'code')),
    active_version_id uuid NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_prompt_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent text NOT NULL REFERENCES agent_prompts(agent),
    version integer NOT NULL CHECK (version > 0),
    title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 100),
    content text NOT NULL CHECK (char_length(btrim(content)) BETWEEN 1 AND 40000),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    updated_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    UNIQUE (agent, version),
    UNIQUE (agent, id)
);
ALTER TABLE agent_prompts ADD CONSTRAINT active_prompt_version_fk
    FOREIGN KEY (agent, active_version_id) REFERENCES agent_prompt_versions(agent, id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION guard_prompt_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.published_at IS NOT NULL THEN
        RAISE EXCEPTION 'Published prompt versions and version history are immutable' USING ERRCODE='23514';
    END IF;
    IF ROW(NEW.id, NEW.agent, NEW.version, NEW.created_at, NEW.created_by)
        IS DISTINCT FROM ROW(OLD.id, OLD.agent, OLD.version, OLD.created_at, OLD.created_by) THEN
        RAISE EXCEPTION 'Prompt version identity is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER guard_prompt_version BEFORE UPDATE OR DELETE ON agent_prompt_versions
    FOR EACH ROW EXECUTE FUNCTION guard_prompt_version();
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_prompt_versions
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_prompts
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

INSERT INTO agent_prompts(agent, active_version_id) VALUES
    ('search', '4c1e1d5a-509a-4da4-ab90-a28c4d686501'),
    ('code', '4c1e1d5a-509a-4da4-ab90-a28c4d686502');

-- Initial built-in instructions. Later edits are new draft versions through the admin API.
INSERT INTO agent_prompt_versions(id, agent, version, title, content, published_at) VALUES
    ('4c1e1d5a-509a-4da4-ab90-a28c4d686501', 'search', 1, 'Default search instructions', $prompt$You answer questions about one application using its documentation and source code.

Answer directly and concisely. Prefer prose, lists, or tables when they explain the answer clearly. Most answers do not need a diagram. Include a diagram only when the user asks for one or it substantially clarifies a complex relationship or flow.

When using Mermaid, prefer a small flowchart, sequenceDiagram, or stateDiagram-v2. Use simple node identifiers and quote labels containing punctuation. Keep citations in the surrounding prose, never in Mermaid code. Use only relationships supported by retrieved evidence. Call validate_mermaid with the exact diagram source before including it in a mermaid code fence. If validation fails, correct the syntax and check again; if it still fails, explain the answer in prose. Always include a prose explanation so the answer remains useful without the diagram.$prompt$, now()),
    ('4c1e1d5a-509a-4da4-ab90-a28c4d686502', 'code', 1, 'Default code analysis instructions', $prompt$You are answering ONE question about the source code of an application, read-only.$prompt$, now());

CREATE VIEW agent_prompt_version_details AS
SELECT v.*, CASE WHEN p.active_version_id=v.id THEN 'active'
                WHEN v.published_at IS NOT NULL THEN 'archived' ELSE 'draft' END AS status
FROM agent_prompt_versions v JOIN agent_prompts p USING (agent);

COMMENT ON TABLE agent_prompts IS 'Exactly one active system prompt per supported agent; activation revision serializes concurrent edits.';
COMMENT ON TABLE agent_prompt_versions IS 'New versions start as drafts. Published content is immutable and can be reactivated.';
