\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned
BEGIN;
SET search_path=ka,public;
CREATE FUNCTION pg_temp.assert_true(ok boolean, label text) RETURNS text LANGUAGE plpgsql AS $$
BEGIN
    IF ok IS DISTINCT FROM true THEN RAISE EXCEPTION 'FAIL: %',label; END IF;
    RETURN 'PASS: ' || label;
END $$;
INSERT INTO projects(slug,name) VALUES ('docs-schema-test','Documentation test') RETURNING id AS project \gset
SELECT pg_temp.assert_true((SELECT count(*)=1 FROM documentation_prompts p
    JOIN documentation_prompt_versions v ON v.id=p.active_version_id
    WHERE p.project_id=:'project' AND p.name='Application documentation (Diátaxis)'
        AND p.enabled AND p.input_kind='code' AND v.version=1 AND v.activated_at IS NOT NULL),
    'new application workspace receives an active app-docs fixture');
SELECT gen_random_uuid() AS doc,gen_random_uuid() AS version \gset
INSERT INTO documents(id,project_id,slug,current_version_id) VALUES (:'doc',:'project','runbook',:'version');
INSERT INTO document_versions(id,document_id,project_id,version,title,description,purpose,audience,markdown,content_hash,source_snapshot,origin)
VALUES (:'version',:'doc',:'project',1,'Runbook','Example','runbook','both','# Test',encode(sha256(convert_to('# Test','UTF8')),'hex'),'{}','manual');
SET CONSTRAINTS ALL IMMEDIATE;
SELECT pg_temp.assert_true((SELECT title='Runbook' FROM document_details WHERE id=:'doc'), 'document has a required version and canonical Markdown');
SELECT pg_temp.assert_true(to_regclass('ka.document_sets') IS NULL AND to_regclass('ka.rag_generations') IS NULL, 'obsolete document and RAG pipeline tables are retired');
INSERT INTO jobs(project_id,kind) VALUES (:'project','documentation_generate') RETURNING id AS job \gset
INSERT INTO documentation_generations(job_id,project_id,mode,instructions,agent_snapshot,prompt_snapshot,source_snapshot)
VALUES (:'job',:'project','create','Write a runbook','{}','[]','{}');
SELECT pg_temp.assert_true((SELECT count(*)=1 FROM documentation_generations WHERE job_id=:'job'), 'generation jobs support multiple document outputs without document sets');
ROLLBACK;
