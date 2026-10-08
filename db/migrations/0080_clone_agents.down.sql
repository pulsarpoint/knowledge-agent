BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Restores the schema 0079 had, empty: configuration versions and prompt history are not restored, and clones lose
-- the link to their original. 0079 down then gives every agent its prompt as the active prompt version.
ALTER TABLE agents DROP COLUMN cloned_from;

CREATE TABLE agent_prompts (
    agent text PRIMARY KEY REFERENCES agents(id),
    active_version_id uuid NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    next_version integer NOT NULL DEFAULT 1 CHECK (next_version > 0)
);
CREATE TABLE agent_prompt_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent text NOT NULL REFERENCES agent_prompts(agent) DEFERRABLE INITIALLY DEFERRED,
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
    FOREIGN KEY (agent, active_version_id) REFERENCES agent_prompt_versions(agent, id) DEFERRABLE INITIALLY DEFERRED;
CREATE FUNCTION guard_prompt_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM ka.agent_prompts WHERE active_version_id = OLD.id) THEN
            RAISE EXCEPTION 'The active prompt version cannot be deleted' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.published_at IS NOT NULL THEN
        RAISE EXCEPTION 'Published prompt content is immutable' USING ERRCODE='23514';
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
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_prompt_versions FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_prompts FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE VIEW agent_prompt_version_details AS
SELECT v.*, CASE WHEN p.active_version_id=v.id THEN 'active'
                WHEN v.published_at IS NOT NULL THEN 'archived' ELSE 'draft' END AS status
FROM agent_prompt_versions v JOIN agent_prompts p USING (agent);
COMMENT ON TABLE agent_prompts IS 'Exactly one active system prompt per supported agent; activation revision serializes concurrent edits.';
COMMENT ON COLUMN agent_prompts.next_version IS 'Next draft number per agent; deleted version numbers are never reused.';
COMMENT ON TABLE agent_prompt_versions IS 'New versions start as drafts. Published content cannot be edited. Inactive versions can be deleted.';

CREATE TABLE agent_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    label text NOT NULL CHECK (length(label) BETWEEN 1 AND 160),
    notes text NOT NULL DEFAULT '',
    config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    created_by uuid NOT NULL REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    source_review_id uuid REFERENCES agent_session_reviews(id),
    UNIQUE (agent, version),
    UNIQUE (id, agent)
);
CREATE INDEX agent_versions_created ON agent_versions(agent, created_at DESC);
ALTER TABLE agents ADD COLUMN active_config_version_id uuid,
    ADD COLUMN next_config_version integer NOT NULL DEFAULT 1 CHECK (next_config_version > 0);
ALTER TABLE agents ADD CONSTRAINT agent_active_config_version
    FOREIGN KEY (active_config_version_id, id) REFERENCES agent_versions(id, agent) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent_sessions ADD COLUMN agent_version_id uuid;
ALTER TABLE agent_sessions ADD CONSTRAINT session_agent_version
    FOREIGN KEY (agent_version_id, agent) REFERENCES agent_versions(id, agent);

COMMIT;
