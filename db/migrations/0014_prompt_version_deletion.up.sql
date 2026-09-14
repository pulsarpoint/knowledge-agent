SET search_path = ka, public;

-- Keep version numbers stable even when the highest numbered draft is deleted.
-- Fresh installs apply migrations in one transaction; flush the deferred seed FK first.
SET CONSTRAINTS ka.active_prompt_version_fk IMMEDIATE;
ALTER TABLE agent_prompts ADD COLUMN next_version integer NOT NULL DEFAULT 1 CHECK (next_version > 0);
SET CONSTRAINTS ka.active_prompt_version_fk DEFERRED;
UPDATE agent_prompts p SET next_version = (
    SELECT coalesce(max(v.version), 0) + 1 FROM agent_prompt_versions v WHERE v.agent = p.agent
);

CREATE OR REPLACE FUNCTION guard_prompt_version() RETURNS trigger LANGUAGE plpgsql AS $$
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

COMMENT ON COLUMN agent_prompts.next_version IS 'Next draft number per agent; deleted version numbers are never reused.';
COMMENT ON TABLE agent_prompt_versions IS 'New versions start as drafts. Published content cannot be edited. Inactive versions can be deleted.';
