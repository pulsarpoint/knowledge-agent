BEGIN;
SET search_path = ka, public;

-- Version 1 is active again where 0102 added version 2 and nobody changed the prompt since (revision 2, next version 3,
-- two versions, version 2 active); otherwise the prompt is left as it is. New workspaces get 0097's text again. Closed
-- runs stay closed.
LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; v1 uuid; v2 uuid; BEGIN
    FOR p IN SELECT * FROM documentation_prompts
             WHERE id = md5('knowledge-agent-v3:prompts:server-discovery:' || project_id::text)::uuid LOOP
        v1 := md5('knowledge-agent-v3:prompts:server-discovery:v1:' || p.project_id::text)::uuid;
        v2 := md5('knowledge-agent-v3:prompts:server-discovery:v2:' || p.project_id::text)::uuid;
        IF p.revision = 2 AND p.next_version = 3 AND p.active_version_id = v2
           AND (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id = p.id) = 2
           AND EXISTS (SELECT 1 FROM documentation_prompt_versions WHERE id = v1 AND prompt_id = p.id) THEN
            UPDATE documentation_prompts SET active_version_id=v1,next_version=2,revision=1 WHERE id=p.id;
            DELETE FROM documentation_prompt_versions WHERE id=v2;
        END IF;
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
        VALUES(v,p,1,$prompt$Explore this server and propose what the inventory should record about it.

1. Read what is already recorded first: get_server_info_basic, get_server_services and get_server_docs. Read the documents that matter with get_document.
2. Explore with read-only commands only, for example: systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a proposal. Describe how something is configured instead.
4. Propose every service you find with propose_service, with its deployment on this server. A service that is already recorded is proposed as an update: keep what is right and correct what is not.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. Finish with a short report: what you found, what you proposed and what you could not check.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;

COMMIT;
