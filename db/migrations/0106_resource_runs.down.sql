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

-- get_resource has no agent_tools rows (every run with resources has it); the old inventory tools' rows removed by
-- 0106 are not restored, so they are off.

-- The Server discovery prompt's get_resource version is removed where nobody changed the prompt since (it is still the
-- active, newest version and the prompt was saved only by adding it); the version before it is active again. New
-- workspaces get 0102's text again.
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT d.* FROM documentation_prompts d JOIN documentation_prompt_versions a ON a.id = d.active_version_id
             WHERE a.id = md5('knowledge-agent-v3:prompts:server-discovery:get-resource:' || d.project_id::text)::uuid
               AND a.version = d.next_version - 1 AND d.revision = d.next_version - 1
               AND (SELECT count(*) FROM documentation_prompt_versions x WHERE x.prompt_id = d.id) = d.next_version - 1 LOOP
        UPDATE documentation_prompts SET active_version_id=(SELECT id FROM documentation_prompt_versions
                WHERE prompt_id=p.id AND version=p.next_version-2),
            next_version=p.next_version-1, revision=p.revision-1
            WHERE id=p.id;
        DELETE FROM documentation_prompt_versions WHERE id=p.active_version_id;
    END LOOP;
END $$;
CREATE OR REPLACE FUNCTION seed_server_discovery_prompt(workspace_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $seed$
DECLARE p uuid; v uuid; inserted uuid;
BEGIN
    p := md5('knowledge-agent-v3:prompts:server-discovery:' || workspace_id::text)::uuid;
    v := md5('knowledge-agent-v3:prompts:server-discovery:v1:' || workspace_id::text)::uuid;
    INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
    VALUES(p,workspace_id,'Server discovery','Explore one server over SSH and propose its services and documents for review.',
           'server',ARRAY['server-discovery','ssh'],2)
    ON CONFLICT DO NOTHING RETURNING id INTO inserted;
    IF inserted IS NOT NULL THEN
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p,1,$prompt$Explore the server named in the context and propose what the inventory should record about it.

1. Read what is already recorded first: get_server_info_basic, get_server_services and get_server_docs. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a proposal. Describe how something is configured instead.
4. Propose every service you find with propose_service, with its deployment on this server. A service that is already recorded is proposed as an update: keep what is right and correct what is not.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. When a proposal is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you proposed and what you could not check.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;

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
-- Discovery runs from before started_from_server had their started_from backfilled by 0106: it is removed again.
UPDATE agent_sessions SET agent_config = agent_config - 'started_from' - 'started_from_backfilled'
    WHERE purpose = 'resource' AND agent_config ? 'started_from_backfilled';
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
