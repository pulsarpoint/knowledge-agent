BEGIN;
SET search_path = ka, public;

LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts
             WHERE id = md5('knowledge-agent-v3:prompts:write-document:' || project_id::text)::uuid LOOP
        IF p.revision <> 1 OR p.next_version <> 2
           OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id = p.id) <> 1 THEN
            RAISE EXCEPTION 'The Write a document prompt has been customized; preserve it before rollback';
        END IF;
        DELETE FROM documentation_prompts WHERE id = p.id;
    END LOOP;
END $$;
DROP TRIGGER seed_write_document ON projects;
DROP FUNCTION seed_write_document_on_project();
DROP FUNCTION seed_write_document_prompt(uuid);

COMMIT;
