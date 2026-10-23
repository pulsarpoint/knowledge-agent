BEGIN;
SET search_path = ka, public;

-- Proposals are gone: an agent drafts documents with new_document / update_document / edit_document and the inventory
-- with save_inventory_draft; a person confirms drafts. Nothing else used the table.
DROP TABLE discovery_proposals;

-- The seeded prompts follow the tools: a new version only where the active, newest version is still the text 0129,
-- 0101 or 0100 seeded (a prompt a person changed is left alone). New workspaces get the new texts as version 1.
LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
CREATE OR REPLACE FUNCTION seed_server_discovery_prompt(workspace_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $seed$
DECLARE p uuid; v uuid; inserted uuid;
BEGIN
    p := md5('knowledge-agent-v3:prompts:server-discovery:' || workspace_id::text)::uuid;
    v := md5('knowledge-agent-v3:prompts:server-discovery:v1:' || workspace_id::text)::uuid;
    INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
    VALUES(p,workspace_id,'Server discovery','Explore one server over SSH, save its services as inventory drafts and draft its documents for review.',
           'server',ARRAY['server-discovery','ssh'],2)
    ON CONFLICT DO NOTHING RETURNING id INTO inserted;
    IF inserted IS NOT NULL THEN
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p,1,$prompt$Explore the server in the Resource line and record what the inventory should hold about it.

1. Read what is already recorded first: get_resource for the server, then for its services. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a draft or a document. Describe how something is configured instead.
4. Save what you find in the inventory as drafts with save_inventory_draft, in one call with the whole picture: the server, every service you find, their deployments on this server, the connections between them and their relations. Name what is already recorded as it is recorded. A person confirms the drafts in the environment workspace.
5. Draft one overview document for the server with new_document (resource: the server): its role, operating system, the services on it and how they connect, ports, important paths and logs. Check with list_documents first: when a document with that title is already linked, change it with update_document (the whole text) or edit_document (one passage) instead.
6. Draft one document for each significant service with new_document (resource: that service): what it does, how it runs, its configuration, its logs and how to check that it is healthy. The same rule: update what exists, do not write it twice.
7. When a draft or a document is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you saved as inventory drafts, which documents you drafted or changed and what you could not check. Say that a person confirms the drafts and publishes the documents; never say anything is published.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;
CREATE OR REPLACE FUNCTION seed_write_document_prompt(workspace_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $seed$
DECLARE p uuid; v uuid; inserted uuid;
BEGIN
    p := md5('knowledge-agent-v3:prompts:write-document:' || workspace_id::text)::uuid;
    v := md5('knowledge-agent-v3:prompts:write-document:v1:' || workspace_id::text)::uuid;
    INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
    VALUES(p,workspace_id,'Write a document','Write one new technical document from the code, documents and servers a run is given, as a draft for review.',
           'mixed',ARRAY['documentation','new-document'],2)
    ON CONFLICT DO NOTHING RETURNING id INTO inserted;
    IF inserted IS NOT NULL THEN
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p,1,$prompt$You write one technical document from the code, documents and servers you were given. Read them before you write, and check facts against them. Never include passwords, keys or tokens.

Rules:
- Write the complete document with update_document; change a passage with edit_document. Each save updates your draft; a person reviews it and decides whether it becomes a document. Never say it is published.
- Read what you wrote with get_document.
- When the request is unclear, ask in plain chat before you write.
- When you are done, say in a few sentences what you wrote.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;
CREATE OR REPLACE FUNCTION seed_document_revision_prompt(workspace_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $seed$
DECLARE p uuid; v uuid; inserted uuid;
BEGIN
    p := md5('knowledge-agent-v3:prompts:document-revision:' || workspace_id::text)::uuid;
    v := md5('knowledge-agent-v3:prompts:document-revision:v1:' || workspace_id::text)::uuid;
    INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
    VALUES(p,workspace_id,'Revise a document','Change one technical document at a person''s request, as a draft for review.',
           'mixed',ARRAY['document-revision'],2)
    ON CONFLICT DO NOTHING RETURNING id INTO inserted;
    IF inserted IS NOT NULL THEN
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p,1,$prompt$You revise one technical document at a person's request. Read the parts of the document you change before you change them, and check facts against the servers, code and documents you were given. Keep the document's structure and voice; change only what the request asks for. Never include passwords, keys or tokens.

Rules:
- Read the document with get_document: without section or lines for its outline and text, with section for one section, with start_line and end_line for lines.
- Change the document only with edit_document (one exact passage) or update_document (the complete text). Each save updates your draft; a person reviews it and decides whether it becomes the live version. Never say the change is live.
- When the request is unclear, ask in plain chat before you change the document.
- When you are done, say in a few sentences what you changed.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;

DO $$ DECLARE p documentation_prompts%ROWTYPE; v uuid; BEGIN
    FOR p IN SELECT d.* FROM documentation_prompts d JOIN documentation_prompt_versions a ON a.id = d.active_version_id
             WHERE d.id = md5('knowledge-agent-v3:prompts:server-discovery:' || d.project_id::text)::uuid
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
        v := md5('knowledge-agent-v3:prompts:server-discovery:no-proposals:' || p.project_id::text)::uuid;
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p.id,p.next_version,$prompt$Explore the server in the Resource line and record what the inventory should hold about it.

1. Read what is already recorded first: get_resource for the server, then for its services. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a draft or a document. Describe how something is configured instead.
4. Save what you find in the inventory as drafts with save_inventory_draft, in one call with the whole picture: the server, every service you find, their deployments on this server, the connections between them and their relations. Name what is already recorded as it is recorded. A person confirms the drafts in the environment workspace.
5. Draft one overview document for the server with new_document (resource: the server): its role, operating system, the services on it and how they connect, ports, important paths and logs. Check with list_documents first: when a document with that title is already linked, change it with update_document (the whole text) or edit_document (one passage) instead.
6. Draft one document for each significant service with new_document (resource: that service): what it does, how it runs, its configuration, its logs and how to check that it is healthy. The same rule: update what exists, do not write it twice.
7. When a draft or a document is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you saved as inventory drafts, which documents you drafted or changed and what you could not check. Say that a person confirms the drafts and publishes the documents; never say anything is published.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v,next_version=p.next_version+1,revision=p.revision+1,
            description=CASE WHEN description='Explore one server over SSH, save its services as inventory drafts and propose its documents for review.' THEN 'Explore one server over SSH, save its services as inventory drafts and draft its documents for review.' ELSE description END
            WHERE id=p.id;
    END LOOP;
END $$;

DO $$ DECLARE p documentation_prompts%ROWTYPE; v uuid; BEGIN
    FOR p IN SELECT d.* FROM documentation_prompts d JOIN documentation_prompt_versions a ON a.id = d.active_version_id
             WHERE d.id = md5('knowledge-agent-v3:prompts:write-document:' || d.project_id::text)::uuid
               AND a.version = d.next_version - 1
               AND a.content = $prompt$You write one technical document from the code, documents and servers you were given. Read them before you write, and check facts against them. Never include passwords, keys or tokens.

Rules:
- Write the complete document with save_document; change a passage with edit_document. Each save updates your draft; a person reviews it and decides whether it becomes a document. Never say it is published.
- Read what you wrote with get_document.
- When the request is unclear, ask in plain chat before you write.
- When you are done, say in a few sentences what you wrote.$prompt$ LOOP
        v := md5('knowledge-agent-v3:prompts:write-document:no-proposals:' || p.project_id::text)::uuid;
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p.id,p.next_version,$prompt$You write one technical document from the code, documents and servers you were given. Read them before you write, and check facts against them. Never include passwords, keys or tokens.

Rules:
- Write the complete document with update_document; change a passage with edit_document. Each save updates your draft; a person reviews it and decides whether it becomes a document. Never say it is published.
- Read what you wrote with get_document.
- When the request is unclear, ask in plain chat before you write.
- When you are done, say in a few sentences what you wrote.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v,next_version=p.next_version+1,revision=p.revision+1
            WHERE id=p.id;
    END LOOP;
END $$;

DO $$ DECLARE p documentation_prompts%ROWTYPE; v uuid; BEGIN
    FOR p IN SELECT d.* FROM documentation_prompts d JOIN documentation_prompt_versions a ON a.id = d.active_version_id
             WHERE d.id = md5('knowledge-agent-v3:prompts:document-revision:' || d.project_id::text)::uuid
               AND a.version = d.next_version - 1
               AND a.content = $prompt$You revise one technical document at a person's request. Read the parts of the document you change before you change them, and check facts against the servers, code and documents you were given. Keep the document's structure and voice; change only what the request asks for. Never include passwords, keys or tokens.

Rules:
- Read the document with get_document: without section or lines for its outline and text, with section for one section, with start_line and end_line for lines.
- Change the document only with edit_document (one exact passage) or save_document (the complete text). Each save updates your draft; a person reviews it and decides whether it becomes the live version. Never say the change is live.
- When the request is unclear, ask in plain chat before you change the document.
- When you are done, say in a few sentences what you changed.$prompt$ LOOP
        v := md5('knowledge-agent-v3:prompts:document-revision:no-proposals:' || p.project_id::text)::uuid;
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p.id,p.next_version,$prompt$You revise one technical document at a person's request. Read the parts of the document you change before you change them, and check facts against the servers, code and documents you were given. Keep the document's structure and voice; change only what the request asks for. Never include passwords, keys or tokens.

Rules:
- Read the document with get_document: without section or lines for its outline and text, with section for one section, with start_line and end_line for lines.
- Change the document only with edit_document (one exact passage) or update_document (the complete text). Each save updates your draft; a person reviews it and decides whether it becomes the live version. Never say the change is live.
- When the request is unclear, ask in plain chat before you change the document.
- When you are done, say in a few sentences what you changed.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v,next_version=p.next_version+1,revision=p.revision+1
            WHERE id=p.id;
    END LOOP;
END $$;

COMMIT;
