BEGIN;
SET search_path = ka, public;

-- Service proposals give way to inventory drafts (0128): an agent writes services, deployments, connections and
-- relations as drafts. Only document proposals stay. A service proposal goes with the document proposals that were
-- about it (service_proposal_id cascades); the column and its constraints go, since only service proposals used it.
DELETE FROM discovery_proposals WHERE kind = 'service';
DROP INDEX discovery_proposals_service_proposal;
ALTER TABLE discovery_proposals
    DROP CONSTRAINT discovery_proposals_document_fields,
    DROP CONSTRAINT discovery_proposals_one_service,
    DROP COLUMN service_proposal_id,
    DROP CONSTRAINT discovery_proposals_kind_check,
    ADD CONSTRAINT discovery_proposals_kind_check CHECK (kind = 'document');
COMMENT ON TABLE discovery_proposals IS
    'What a run proposes for a person to review: a document (service_id: the service it is about; document_id: the document it becomes a new version of). Inventory findings are drafts instead (status on the inventory tables). Accepted or rejected by an administrator.';

-- The seeded Server discovery prompt saves inventory findings as drafts with save_inventory_draft (documents are still
-- proposed with propose_document). Where its active text is still 0106's seed, a new version with the rewritten text
-- becomes active (the rest of the prompt is kept); a prompt whose text a person changed is left alone. Its description
-- follows where it is still the seeded one. New workspaces get the new text as version 1.
CREATE OR REPLACE FUNCTION seed_server_discovery_prompt(workspace_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $seed$
DECLARE p uuid; v uuid; inserted uuid;
BEGIN
    p := md5('knowledge-agent-v3:prompts:server-discovery:' || workspace_id::text)::uuid;
    v := md5('knowledge-agent-v3:prompts:server-discovery:v1:' || workspace_id::text)::uuid;
    INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
    VALUES(p,workspace_id,'Server discovery','Explore one server over SSH, save its services as inventory drafts and propose its documents for review.',
           'server',ARRAY['server-discovery','ssh'],2)
    ON CONFLICT DO NOTHING RETURNING id INTO inserted;
    IF inserted IS NOT NULL THEN
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p,1,$prompt$Explore the server in the Resource line and record what the inventory should hold about it.

1. Read what is already recorded first: get_resource for the server, then for its services. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a draft or a proposal. Describe how something is configured instead.
4. Save what you find in the inventory as drafts with save_inventory_draft, in one call with the whole picture: the server, every service you find, their deployments on this server, the connections between them and their relations. Name what is already recorded as it is recorded. A person confirms the drafts in the environment workspace.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. When a draft or a proposal is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you saved as drafts, what you proposed and what you could not check.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;

LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; v uuid; BEGIN
    FOR p IN SELECT d.* FROM documentation_prompts d JOIN documentation_prompt_versions a ON a.id = d.active_version_id
             WHERE d.id = md5('knowledge-agent-v3:prompts:server-discovery:' || d.project_id::text)::uuid
               AND a.content = $prompt$Explore the server in the Resource line and propose what the inventory should record about it.

1. Read what is already recorded first: get_resource for the server, then for its services. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a proposal. Describe how something is configured instead.
4. Propose every service you find with propose_service, with its deployment on this server. A service that is already recorded is proposed as an update: keep what is right and correct what is not.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. When a proposal is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you proposed and what you could not check.$prompt$ LOOP
        v := md5('knowledge-agent-v3:prompts:server-discovery:inventory-drafts:' || p.project_id::text)::uuid;
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p.id,p.next_version,$prompt$Explore the server in the Resource line and record what the inventory should hold about it.

1. Read what is already recorded first: get_resource for the server, then for its services. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a draft or a proposal. Describe how something is configured instead.
4. Save what you find in the inventory as drafts with save_inventory_draft, in one call with the whole picture: the server, every service you find, their deployments on this server, the connections between them and their relations. Name what is already recorded as it is recorded. A person confirms the drafts in the environment workspace.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. When a draft or a proposal is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you saved as drafts, what you proposed and what you could not check.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v,next_version=p.next_version+1,revision=p.revision+1,
            description=CASE WHEN description='Explore one server over SSH and propose its services and documents for review.'
                THEN 'Explore one server over SSH, save its services as inventory drafts and propose its documents for review.' ELSE description END
            WHERE id=p.id;
    END LOOP;
END $$;

COMMIT;
