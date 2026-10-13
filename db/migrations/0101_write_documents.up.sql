BEGIN;
SET search_path = ka, public;

-- Documents are written with the document tools: how an agent writes a new document is its prompt's text, in the prompt
-- library ("Write a document", tags documentation and new-document), not a final-answer rule the code appends. Open
-- Generate documents runs were pinned with the old rule, so they are closed.
CREATE FUNCTION seed_write_document_prompt(workspace_id uuid) RETURNS void
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
- Write the complete document with save_document; change a passage with edit_document. Each save updates your draft; a person reviews it and decides whether it becomes a document. Never say it is published.
- Read what you wrote with get_document.
- When the request is unclear, ask in plain chat before you write.
- When you are done, say in a few sentences what you wrote.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;
CREATE FUNCTION seed_write_document_on_project() RETURNS trigger
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $$
BEGIN
    PERFORM seed_write_document_prompt(NEW.id);
    RETURN NEW;
END $$;
CREATE TRIGGER seed_write_document AFTER INSERT ON projects
    FOR EACH ROW EXECUTE FUNCTION seed_write_document_on_project();
SELECT seed_write_document_prompt(id) FROM projects;

UPDATE agent_session_inputs SET status='discarded'
    WHERE status='queued' AND session_id IN (SELECT id FROM agent_sessions WHERE purpose='documents' AND status<>'closed');
UPDATE agent_sessions SET status='closed', closed_at=now(), status_message='Closed by an update; start a new chat.'
    WHERE purpose='documents' AND status<>'closed';

COMMIT;
