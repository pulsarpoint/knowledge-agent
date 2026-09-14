BEGIN;
SET search_path=ka,public;
DROP TRIGGER seed_app_docs_prompt ON projects;
LOCK TABLE documentation_prompts,documentation_prompt_versions IN SHARE ROW EXCLUSIVE MODE;

-- Remove only this fixture, and refuse to discard administrator changes.
DO $$
DECLARE
    prompt documentation_prompts%ROWTYPE;
    version_id uuid;
BEGIN
    FOR prompt IN SELECT * FROM documentation_prompts
        WHERE id=md5('knowledge-agent-v3:documentation:app-docs:' || project_id::text)::uuid
    LOOP
        version_id := md5('knowledge-agent-v3:documentation:app-docs:v1:' || prompt.project_id::text)::uuid;
        IF prompt.revision<>1 OR prompt.next_version<>2 OR NOT prompt.enabled
            OR prompt.name<>'Application documentation (Diátaxis)' OR prompt.input_kind<>'code'
            OR prompt.description<>'Code-backed tutorials, how-to guides, reference and architecture explanations. Adapted from the v2 app-docs plugin for managed document versions.'
            OR prompt.active_version_id IS DISTINCT FROM version_id
            OR (SELECT count(*) FROM documentation_prompt_versions WHERE prompt_id=prompt.id)<>1
            OR NOT EXISTS (SELECT 1 FROM documentation_prompt_versions
                WHERE id=version_id AND prompt_id=prompt.id AND version=1
                    AND activated_at IS NOT NULL AND created_by IS NULL
                    AND encode(sha256(convert_to(content,'UTF8')),'hex')='79ed875b7e89b47c4d1db6042fe00cee7cd7b732230cc8be2760af54e5b88364') THEN
            RAISE EXCEPTION 'The app-docs prompt has been customized; export/migrate it before rollback';
        END IF;
        UPDATE documentation_prompts SET active_version_id=NULL WHERE id=prompt.id;
        DELETE FROM documentation_prompts WHERE id=prompt.id;
    END LOOP;
END $$;
DROP FUNCTION seed_app_docs_prompt_on_project();
DROP FUNCTION seed_app_docs_prompt(uuid);
COMMIT;
