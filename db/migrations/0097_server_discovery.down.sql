BEGIN;
SET search_path = ka, public;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agent_sessions WHERE purpose = 'discovery') THEN
        RAISE EXCEPTION 'Discovery runs exist; delete them (and their proposals) before rollback';
    END IF;
END $$;
LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts
             WHERE id = md5('knowledge-agent-v3:prompts:server-discovery:' || project_id::text)::uuid LOOP
        IF p.revision <> 1 OR p.next_version <> 2
           OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id = p.id) <> 1 THEN
            RAISE EXCEPTION 'The server discovery prompt has been customized; preserve it before rollback';
        END IF;
        DELETE FROM documentation_prompts WHERE id = p.id;
    END LOOP;
END $$;
DROP TRIGGER seed_server_discovery ON projects;
DROP FUNCTION seed_server_discovery_on_project();
DROP FUNCTION seed_server_discovery_prompt(uuid);
DROP TABLE discovery_proposals;
DROP INDEX agent_sessions_discovery;
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_discovery_server,
    DROP CONSTRAINT agent_sessions_ad_hoc,
    ADD CONSTRAINT agent_sessions_ad_hoc CHECK (agent_id IS NOT NULL OR purpose = 'documents'),
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event')),
    DROP COLUMN server_id;
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent).';

COMMIT;
