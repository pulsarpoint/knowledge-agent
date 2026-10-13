BEGIN;
SET search_path = ka, public;

LOCK TABLE documentation_prompts, documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE p documentation_prompts%ROWTYPE; BEGIN
    FOR p IN SELECT * FROM documentation_prompts
             WHERE id = md5('knowledge-agent-v3:prompts:document-revision:' || project_id::text)::uuid LOOP
        IF p.revision <> 1 OR p.next_version <> 2
           OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id = p.id) <> 1 THEN
            RAISE EXCEPTION 'The Revise a document prompt has been customized; preserve it before rollback';
        END IF;
        DELETE FROM documentation_prompts WHERE id = p.id;
    END LOOP;
END $$;
DROP TRIGGER seed_document_revision ON projects;
DROP FUNCTION seed_document_revision_on_project();
DROP FUNCTION seed_document_revision_prompt(uuid);
COMMENT ON COLUMN agent_sessions.target_document_id IS 'The document an Ask agent run revises; set exactly when purpose is revision.';

COMMIT;
