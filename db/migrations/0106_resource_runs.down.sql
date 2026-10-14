BEGIN;
SET search_path = ka, public;

-- Server runs become discovery runs again (their tags are dropped); runs started from a service or an environment,
-- and proposals without a server, cannot be kept: rollback refuses. Seeded assistant prompts are removed when nobody
-- changed them; a customized one refuses rollback.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agent_sessions WHERE purpose = 'resource' AND server_id IS NULL) THEN
        RAISE EXCEPTION 'Runs started from a service or an environment exist; delete them before rollback';
    END IF;
    IF EXISTS (SELECT 1 FROM discovery_proposals WHERE server_id IS NULL) THEN
        RAISE EXCEPTION 'Proposals without a server exist; delete them before rollback';
    END IF;
END $$;
LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts
             WHERE id IN (md5('knowledge-agent-v3:prompts:server-assistant:' || project_id::text)::uuid,
                          md5('knowledge-agent-v3:prompts:service-assistant:' || project_id::text)::uuid,
                          md5('knowledge-agent-v3:prompts:environment-assistant:' || project_id::text)::uuid) LOOP
        IF p.revision <> 1 OR p.next_version <> 2
           OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id = p.id) <> 1 THEN
            RAISE EXCEPTION 'The % prompt has been customized; preserve it before rollback', p.name;
        END IF;
        DELETE FROM documentation_prompts WHERE id = p.id;
    END LOOP;
END $$;
DROP TRIGGER seed_assistant_prompts ON projects;
DROP FUNCTION seed_assistant_prompts_on_project();
DROP FUNCTION seed_assistant_prompts(uuid);
ALTER TABLE documentation_prompts
    DROP CONSTRAINT documentation_prompts_input_kind_check,
    ADD CONSTRAINT documentation_prompts_input_kind_check CHECK (input_kind IN ('code', 'server', 'service', 'mixed'));

ALTER TABLE discovery_proposals ALTER COLUMN server_id SET NOT NULL;
COMMENT ON COLUMN discovery_proposals.server_id IS NULL;

ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_resource_server,
    DROP CONSTRAINT agent_sessions_ad_hoc,
    DROP CONSTRAINT agent_sessions_purpose;
UPDATE agent_sessions SET agent_config = (agent_config - 'started_from' - 'proposal_tools') || jsonb_build_object(
        'started_from_server', jsonb_build_object('id', agent_config->'started_from'->'id',
                                                  'name', agent_config->'started_from'->'name'))
    WHERE purpose = 'resource' AND agent_config ? 'started_from';
UPDATE agent_sessions SET purpose = 'discovery' WHERE purpose = 'resource';
DROP INDEX agent_sessions_tags_gin;
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_tags,
    DROP COLUMN tags,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event', 'discovery', 'revision')),
    ADD CONSTRAINT agent_sessions_ad_hoc CHECK (agent_id IS NOT NULL OR purpose IN ('documents', 'discovery', 'revision')),
    ADD CONSTRAINT agent_sessions_discovery_server CHECK ((purpose = 'discovery') = (server_id IS NOT NULL));
DROP FUNCTION agent_session_tags_valid(text[]);
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent); discovery: a server discovery run (server_id); revision: an Ask agent run on a document (target_document_id).';
COMMENT ON COLUMN agent_sessions.server_id IS 'The server a discovery run explores; set exactly when purpose is discovery.';

COMMIT;
