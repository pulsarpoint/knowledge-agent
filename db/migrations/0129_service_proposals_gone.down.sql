BEGIN;
SET search_path = ka, public;
-- Back to 0128: the shape only (service proposals and their links are not restored).

ALTER TABLE discovery_proposals
    DROP CONSTRAINT discovery_proposals_kind_check,
    ADD COLUMN service_proposal_id uuid REFERENCES discovery_proposals(id) ON DELETE CASCADE,
    ADD CONSTRAINT discovery_proposals_kind_check CHECK (kind IN ('service', 'document')),
    ADD CONSTRAINT discovery_proposals_document_fields CHECK (kind = 'document' OR (service_proposal_id IS NULL AND document_id IS NULL)),
    ADD CONSTRAINT discovery_proposals_one_service CHECK (service_id IS NULL OR service_proposal_id IS NULL);
CREATE INDEX discovery_proposals_service_proposal ON discovery_proposals(service_proposal_id) WHERE service_proposal_id IS NOT NULL;
COMMENT ON TABLE discovery_proposals IS
    'What a discovery run proposes for the inventory: a service (service_id: the existing service it updates) or a document (service_id or service_proposal_id: the service it is about, else the server; document_id: the document it becomes a new version of). Accepted or rejected by an administrator.';

-- The Server discovery prompt's inventory-drafts version is removed where it is still the active, newest version and
-- holds 0129's text; the version before it is active again (and the seeded description is back). A prompt a person
-- changed since is left alone, and so are workspaces created after 0129 (their version 1 has the new text). New
-- workspaces get 0106's text again.
LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT d.* FROM documentation_prompts d JOIN documentation_prompt_versions a ON a.id = d.active_version_id
             WHERE a.id = md5('knowledge-agent-v3:prompts:server-discovery:inventory-drafts:' || d.project_id::text)::uuid
               AND a.version = d.next_version - 1
               AND a.content = $prompt$Explore the server in the Resource line and record what the inventory should hold about it.

1. Read what is already recorded first: get_resource for the server, then for its services. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a draft or a proposal. Describe how something is configured instead.
4. Save what you find in the inventory as drafts with save_inventory_draft, in one call with the whole picture: the server, every service you find, their deployments on this server, the connections between them and their relations. Name what is already recorded as it is recorded. A person confirms the drafts in the environment workspace.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. When a draft or a proposal is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you saved as drafts, what you proposed and what you could not check.$prompt$ LOOP
        UPDATE documentation_prompts SET active_version_id=(SELECT id FROM documentation_prompt_versions
                WHERE prompt_id=p.id AND version=p.next_version-2),
            next_version=p.next_version-1, revision=p.revision+1,
            description=CASE WHEN description='Explore one server over SSH, save its services as inventory drafts and propose its documents for review.'
                THEN 'Explore one server over SSH and propose its services and documents for review.' ELSE description END
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

COMMIT;
