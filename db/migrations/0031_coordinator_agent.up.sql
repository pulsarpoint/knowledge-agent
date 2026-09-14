BEGIN;
ALTER TABLE ka.agents ADD COLUMN is_coordinator boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX one_coordinator_agent ON ka.agents(is_coordinator) WHERE is_coordinator;
ALTER TABLE ka.agents ADD CONSTRAINT coordinator_delegates_resources CHECK (
    NOT is_coordinator OR NOT (documents_enabled OR repositories_enabled OR ssh_enabled
        OR skills_enabled OR mcp_enabled OR services_enabled)
);

INSERT INTO ka.agents(id,name,description,when_to_use,is_coordinator,model_id,updated_by)
SELECT 'coordinator','Coordinator','Coordinate the selected specialists and reply to the user.',
    'The entry point for every task on the multi-agent board.',true,model_id,updated_by
FROM ka.coordinator_settings;
INSERT INTO ka.agent_prompts(agent,active_version_id,next_version)
VALUES ('coordinator',gen_random_uuid(),2);
INSERT INTO ka.agent_prompt_versions(id,agent,version,title,content,published_at,created_by,updated_by)
SELECT p.active_version_id,p.agent,1,'Coordinator system prompt',s.system_prompt,now(),s.updated_by,s.updated_by
FROM ka.agent_prompts p CROSS JOIN ka.coordinator_settings s WHERE p.agent='coordinator';

ALTER TABLE ka.coordinator_settings
    ADD COLUMN agent_id text NOT NULL DEFAULT 'coordinator' REFERENCES ka.agents(id),
    ADD COLUMN notes text NOT NULL DEFAULT '' CHECK (length(notes)<=40000),
    DROP COLUMN model_id,
    DROP COLUMN system_prompt;
ALTER TABLE ka.coordinator_settings ALTER COLUMN agent_id DROP DEFAULT;

CREATE FUNCTION ka.protect_coordinator_agent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        IF OLD.is_coordinator THEN
            RAISE EXCEPTION 'The coordinator cannot be removed' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.is_coordinator IS DISTINCT FROM OLD.is_coordinator THEN
        RAISE EXCEPTION 'The coordinator flag cannot be changed' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER protect_coordinator_agent BEFORE UPDATE OR DELETE ON ka.agents
    FOR EACH ROW EXECUTE FUNCTION ka.protect_coordinator_agent();
COMMENT ON TABLE ka.coordinator_settings IS 'Coordinator notes and execution limits; identity, model and prompt versions belong to its agent record.';
COMMIT;
