BEGIN;
SET search_path = ka, public;

-- A chat on a server, a service or an environment is a run tagged with that resource (purpose resource): tags
-- servers/<id>, services/<id>, environments/<id>; wizard runs also carry proposals. server_id stays the pinned server
-- of server runs only. Discovery runs become resource runs of their server. Proposals may come from service and
-- environment runs (no server). Each resource type gets a seeded, editable "<Type> assistant" prompt, the default
-- prompt of a new chat on it.
CREATE FUNCTION agent_session_tags_valid(tags text[]) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT NOT EXISTS (
        SELECT 1 FROM unnest(tags) AS tag
        WHERE tag IS NULL OR length(tag) > 120 OR tag !~ '^[a-z0-9_-]+(/[a-z0-9_-]+)*$'
    ) AND cardinality(tags) = (SELECT count(DISTINCT tag) FROM unnest(tags) AS tag)
$$;
ALTER TABLE agent_sessions
    ADD COLUMN tags text[] NOT NULL DEFAULT '{}',
    ADD CONSTRAINT agent_sessions_tags CHECK (cardinality(tags) <= 10 AND agent_session_tags_valid(tags)),
    DROP CONSTRAINT agent_sessions_discovery_server,
    DROP CONSTRAINT agent_sessions_purpose,
    DROP CONSTRAINT agent_sessions_ad_hoc;
CREATE INDEX agent_sessions_tags_gin ON agent_sessions USING gin (tags);

-- A discovery run's pinned server becomes its started_from; the proposal tools it had on were the ones its request
-- turned on (an ad-hoc run's tools are exactly its request's), so they stay offered.
UPDATE agent_sessions SET agent_config = (agent_config - 'started_from_server') || jsonb_build_object(
        'started_from', jsonb_build_object('type', 'server', 'id', agent_config->'started_from_server'->'id',
                                           'name', agent_config->'started_from_server'->'name'),
        'proposal_tools', (SELECT coalesce(jsonb_agg(item->'tool' ORDER BY item->>'tool' DESC), '[]'::jsonb)
                           FROM jsonb_array_elements(agent_config->'tools'->'items') AS item
                           WHERE item->>'tool' IN ('propose_service', 'propose_document') AND item->>'mode' = 'on'))
    WHERE purpose = 'discovery' AND agent_config ? 'started_from_server';
UPDATE agent_sessions SET purpose = 'resource', tags = ARRAY['servers/' || server_id::text] WHERE purpose = 'discovery';

ALTER TABLE agent_sessions
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event', 'resource', 'revision')),
    ADD CONSTRAINT agent_sessions_ad_hoc CHECK (agent_id IS NOT NULL OR purpose IN ('documents', 'resource', 'revision')),
    ADD CONSTRAINT agent_sessions_resource_server CHECK (server_id IS NULL OR purpose = 'resource');
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a new-document run (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent); resource: a chat on a server, service or environment (its tag says which); revision: an Ask agent run on a document.';
COMMENT ON COLUMN agent_sessions.server_id IS 'The server a resource run started from; set only for runs started from a server.';
COMMENT ON COLUMN agent_sessions.tags IS
    'Labels lists find runs by: the resource a run started from (servers/<id>, services/<id>, environments/<id>) and proposals for Propose wizard runs.';

ALTER TABLE discovery_proposals ALTER COLUMN server_id DROP NOT NULL;
COMMENT ON COLUMN discovery_proposals.server_id IS 'The server of the run that proposed it; null for runs started from a service or an environment.';

ALTER TABLE documentation_prompts
    DROP CONSTRAINT documentation_prompts_input_kind_check,
    ADD CONSTRAINT documentation_prompts_input_kind_check CHECK (input_kind IN ('code', 'server', 'service', 'environment', 'mixed'));

CREATE FUNCTION seed_assistant_prompts(workspace_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $seed$
DECLARE kind text; p uuid; v uuid; inserted uuid; label text; summary text; body text;
BEGIN
    FOREACH kind IN ARRAY ARRAY['server', 'service', 'environment'] LOOP
        p := md5('knowledge-agent-v3:prompts:' || kind || '-assistant:' || workspace_id::text)::uuid;
        v := md5('knowledge-agent-v3:prompts:' || kind || '-assistant:v1:' || workspace_id::text)::uuid;
        label := initcap(kind) || ' assistant';
        summary := 'Answer a question about one ' || kind || ' with the smallest read-only check that answers it.';
        body := CASE kind
            WHEN 'server' THEN $prompt$Answer the person's question about the server in the Resource line.

1. Use the smallest read-only command that answers it, with ssh_run on that server: for example df -h for disk usage, free -h for memory, uptime for load, systemctl is-active <unit> for a service. Show the result.
2. Read what is recorded when it helps: get_resource for the server (its instructions, services and documents), then for what it links to.
3. Do not explore beyond the question. Never change, install, restart or delete anything.
4. Never show passwords, keys or tokens.$prompt$
            WHEN 'service' THEN $prompt$Answer the person's question about the service in the Resource line.

1. Read what is recorded when it helps: get_resource for the service. Its links say which servers it is deployed on.
2. When the answer needs a live check, use the smallest read-only command that answers it, with ssh_run on a server it is deployed on: for example systemctl is-active <unit> for its state, df -h for disk usage, free -h for memory, uptime for load. Show the result.
3. Do not explore beyond the question. Never change, install, restart or delete anything.
4. Never show passwords, keys or tokens.$prompt$
            ELSE $prompt$Answer the person's question about the environment in the Resource line.

1. Read what is recorded when it helps: get_resource for the environment lists its services and servers; get_resource for each of them when the question needs it.
2. When the answer needs a live check, use the smallest read-only command that answers it, with ssh_run on one of its servers: for example df -h for disk usage, free -h for memory, uptime for load, systemctl is-active <unit> for a service. Show the result.
3. Do not explore beyond the question. Never change, install, restart or delete anything.
4. Never show passwords, keys or tokens.$prompt$
        END;
        inserted := NULL;
        INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
        VALUES(p,workspace_id,label,summary,kind,ARRAY['assistant',kind],2)
        ON CONFLICT DO NOTHING RETURNING id INTO inserted;
        IF inserted IS NOT NULL THEN
            INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
            VALUES(v,p,1,body,now());
            UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
        END IF;
    END LOOP;
END $seed$;
CREATE FUNCTION seed_assistant_prompts_on_project() RETURNS trigger
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $$
BEGIN
    PERFORM seed_assistant_prompts(NEW.id);
    RETURN NEW;
END $$;
CREATE TRIGGER seed_assistant_prompts AFTER INSERT ON projects
    FOR EACH ROW EXECUTE FUNCTION seed_assistant_prompts_on_project();
SELECT seed_assistant_prompts(id) FROM projects;

-- One inventory tool: get_resource replaces get_server_info_basic, get_server_services, get_server_docs,
-- get_service_info, get_service_docs and get_project_info. A saved agent that had any of them on gets get_resource on.
INSERT INTO agent_tools(agent_id, tool, mode, subagent_allowed)
SELECT agent_id, 'get_resource', 'on', bool_and(subagent_allowed) FROM agent_tools
    WHERE tool IN ('get_server_info_basic', 'get_server_services', 'get_server_docs', 'get_service_info',
                   'get_service_docs', 'get_project_info') AND mode = 'on'
    GROUP BY agent_id
ON CONFLICT (agent_id, tool) DO UPDATE SET mode = 'on';
DELETE FROM agent_tools WHERE tool IN ('get_server_info_basic', 'get_server_services', 'get_server_docs',
    'get_service_info', 'get_service_docs', 'get_project_info');

-- The seeded Server discovery prompt reads with get_resource: a new version where nobody changed it (its active version
-- is the newest, holds 0102's text, and the prompt was saved only by adding versions); a customized prompt is left
-- alone. New workspaces get the new text as version 1.
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
        VALUES(v,p,1,$prompt$Explore the server in the Resource line and propose what the inventory should record about it.

1. Read what is already recorded first: get_resource for the server, then for its services. Read the documents that matter with get_document.
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
    FOR p IN SELECT d.* FROM documentation_prompts d JOIN documentation_prompt_versions a ON a.id = d.active_version_id
             WHERE d.id = md5('knowledge-agent-v3:prompts:server-discovery:' || d.project_id::text)::uuid
               AND a.version = d.next_version - 1 AND d.revision = d.next_version - 1
               AND (SELECT count(*) FROM documentation_prompt_versions x WHERE x.prompt_id = d.id) = d.next_version - 1
               AND a.content = $prompt$Explore the server named in the context and propose what the inventory should record about it.

1. Read what is already recorded first: get_server_info_basic, get_server_services and get_server_docs. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a proposal. Describe how something is configured instead.
4. Propose every service you find with propose_service, with its deployment on this server. A service that is already recorded is proposed as an update: keep what is right and correct what is not.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. When a proposal is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you proposed and what you could not check.$prompt$ LOOP
        v := md5('knowledge-agent-v3:prompts:server-discovery:get-resource:' || p.project_id::text)::uuid;
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p.id,p.next_version,$prompt$Explore the server in the Resource line and propose what the inventory should record about it.

1. Read what is already recorded first: get_resource for the server, then for its services. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a proposal. Describe how something is configured instead.
4. Propose every service you find with propose_service, with its deployment on this server. A service that is already recorded is proposed as an update: keep what is right and correct what is not.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. When a proposal is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you proposed and what you could not check.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v,next_version=p.next_version+1,revision=p.revision+1
            WHERE id=p.id;
    END LOOP;
END $$;

COMMIT;
