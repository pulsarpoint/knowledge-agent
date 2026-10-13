BEGIN;
SET search_path = ka, public;

-- A discovery run is an ordinary run started from a server (/api/sessions, started_from.server_id): what it does is its
-- prompt's text, not a system prompt and final rule the code adds. The seeded "Server discovery" prompt gets that text
-- as version 2 when nobody changed it (0097's guard: revision 1, next version 2, one version); a customized prompt is
-- left alone. New workspaces get the new text as version 1. Open discovery runs were pinned with the old prompt and
-- the pinned discovery key, so they are closed; their proposals stay reviewable.
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

LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; v uuid; BEGIN
    FOR p IN SELECT * FROM documentation_prompts
             WHERE id = md5('knowledge-agent-v3:prompts:server-discovery:' || project_id::text)::uuid LOOP
        IF p.revision = 1 AND p.next_version = 2
           AND (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id = p.id) = 1 THEN
            v := md5('knowledge-agent-v3:prompts:server-discovery:v2:' || p.project_id::text)::uuid;
            INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
            VALUES(v,p.id,2,$prompt$Explore the server named in the context and propose what the inventory should record about it.

1. Read what is already recorded first: get_server_info_basic, get_server_services and get_server_docs. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a proposal. Describe how something is configured instead.
4. Propose every service you find with propose_service, with its deployment on this server. A service that is already recorded is proposed as an update: keep what is right and correct what is not.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. When a proposal is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you proposed and what you could not check.$prompt$,now());
            UPDATE documentation_prompts SET active_version_id=v,next_version=3,revision=2 WHERE id=p.id;
        END IF;
    END LOOP;
END $$;

UPDATE agent_session_inputs SET status='discarded'
    WHERE status='queued' AND session_id IN (SELECT id FROM agent_sessions WHERE purpose='discovery' AND status<>'closed');
UPDATE agent_sessions SET status='closed', closed_at=now(), status_message='Closed by an update; start a new chat.'
    WHERE purpose='discovery' AND status<>'closed';

COMMIT;
