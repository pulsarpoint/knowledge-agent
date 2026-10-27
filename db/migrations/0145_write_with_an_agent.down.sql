BEGIN;
SET search_path = ka, public;

-- Back to 0144: agent_sessions.document_name (at the end of the table) and its check; a new-document run needs its
-- document name, so existing ones get their title (test data). The audit index goes. The Write a document prompt's
-- 0145 version is removed where it is still the active, newest, untouched one, and new workspaces get the 0131 text.
DROP INDEX audit_events_draft_session;

ALTER TABLE agent_sessions DROP CONSTRAINT agent_sessions_documentation;
ALTER TABLE agent_sessions ADD COLUMN document_name text CONSTRAINT agent_sessions_document_name_check
    CHECK (char_length(btrim(document_name)) >= 1 AND char_length(btrim(document_name)) <= 240);
UPDATE agent_sessions SET document_name = title WHERE purpose = 'documents';
ALTER TABLE agent_sessions ADD CONSTRAINT agent_sessions_documentation CHECK (
    CASE purpose
        WHEN 'documents' THEN document_name IS NOT NULL
        ELSE document_name IS NULL AND draft IS NULL AND draft_turn IS NULL AND document_id IS NULL
    END);
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a new-document run (document name, draft); event: started by an event handler (Send to agent); resource: a chat on a server, service or environment (its tag says which); revision: an Ask agent run on a document; chat: a saved agent''s conversation (its Chat tab, Run agent).';

DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT d.* FROM documentation_prompts d JOIN documentation_prompt_versions a ON a.id = d.active_version_id
             WHERE a.id = md5('knowledge-agent-v3:prompts:write-document:write-with-an-agent:' || d.project_id::text)::uuid
               AND a.version = d.next_version - 1
               AND a.content = $prompt$You write technical documents from the code, documents and servers you were given. Read them before you write, and check facts against them. Never include passwords, keys or tokens.

Rules:
- Before you write, look for a document on the subject with list_documents or search_documents: change it instead of writing a second one.
- Create a document with new_document, for the server, service, environment or code project it is about. Change it, or an existing document, with update_document (the complete text) or edit_document (one passage). Each save updates your draft; a person reviews it and decides whether it becomes a document. Never say it is published.
- A person may mention a draft as [Draft "<title>" · <id>]: use that id with get_document, update_document and edit_document.
- Read what you wrote with get_document.
- When the request is unclear, ask in plain chat before you write.
- When you are done, say in a few sentences what you wrote.$prompt$ LOOP
        UPDATE documentation_prompts SET active_version_id=(SELECT id FROM documentation_prompt_versions
                WHERE prompt_id=p.id AND version=p.next_version-2),
            next_version=p.next_version-1, revision=p.revision+1,
            description=CASE WHEN description='Write new technical documents from the code, documents and servers a run is given, as drafts for review.' THEN 'Write one new technical document from the code, documents and servers a run is given, as a draft for review.' ELSE description END
            WHERE id=p.id;
        DELETE FROM documentation_prompt_versions WHERE id=p.active_version_id;
    END LOOP;
END $$;

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

COMMIT;
