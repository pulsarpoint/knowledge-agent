BEGIN;
SET search_path = ka, public;

-- One sessions API: how an Ask agent run works is now its prompt's text, in the prompt library ("Revise a document",
-- tag document-revision), not text the code appends. The document tools became get_document, edit_document and
-- save_document, taking a document id; open Ask agent runs were pinned with the old tools, so they are closed.
CREATE FUNCTION seed_document_revision_prompt(workspace_id uuid) RETURNS void
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
- Change the document only with edit_document (one exact passage) or save_document (the complete text). Each save updates your draft; a person reviews it and decides whether it becomes the live version. Never say the change is live.
- When the request is unclear, ask in plain chat before you change the document.
- When you are done, say in a few sentences what you changed.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;
CREATE FUNCTION seed_document_revision_on_project() RETURNS trigger
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $$
BEGIN
    PERFORM seed_document_revision_prompt(NEW.id);
    RETURN NEW;
END $$;
CREATE TRIGGER seed_document_revision AFTER INSERT ON projects
    FOR EACH ROW EXECUTE FUNCTION seed_document_revision_on_project();
SELECT seed_document_revision_prompt(id) FROM projects;

UPDATE agent_session_inputs SET status='discarded'
    WHERE status='queued' AND session_id IN (SELECT id FROM agent_sessions WHERE purpose='revision' AND status<>'closed');
UPDATE agent_sessions SET status='closed', closed_at=now(), status_message='Closed by an update; start a new chat.'
    WHERE purpose='revision' AND status<>'closed';
COMMENT ON COLUMN agent_sessions.target_document_id IS
    'The document an Ask agent run was started from (for lists); set exactly when purpose is revision. The agent and its tools never read it.';

COMMIT;
