BEGIN;
SET search_path = ka, public;

-- Two built-in prompts for code projects, in every workspace once (seeded like the assistant prompts, 0106, and Server
-- discovery, 0097): the existing ones now, new ones on creation.
-- "Code project assistant" (tags assistant, code-project): a chat on a code project's default prompt; it answers
-- questions about the repository and drafts documents only when asked.
-- "Code project overview" (tags code-project-discovery, overview): what Discover… offers on a code project; it reads
-- the repository and drafts its overview document.
CREATE FUNCTION seed_code_project_prompts(workspace_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $seed$
DECLARE p uuid; v uuid; inserted uuid;
BEGIN
    p := md5('knowledge-agent-v3:prompts:code-project-assistant:' || workspace_id::text)::uuid;
    v := md5('knowledge-agent-v3:prompts:code-project-assistant:v1:' || workspace_id::text)::uuid;
    INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
    VALUES(p,workspace_id,'Code project assistant','Answer a question about one code project by reading only the code that answers it.',
           'code',ARRAY['assistant','code-project'],2)
    ON CONFLICT DO NOTHING RETURNING id INTO inserted;
    IF inserted IS NOT NULL THEN
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p,1,$prompt$Answer the person's question about the code project in the Resource line.

1. Read only what answers it, in the project's folders: search_files and list_files to find the code, read_file to read it, code_graph for symbols, callers and dependencies. Use shell_run for read-only commands in the folders (for example git log or a line count) when that is quicker. Show the files and lines your answer rests on.
2. Read what is recorded when it helps: get_resource for the code project, list_documents for its documents.
3. Do not explore beyond the question. Never change, install or delete anything.
4. Write or change a document only when the person asks: new_document for the code project, or update_document of an existing one. Its documents are drafts a person reviews.
5. Never show passwords, keys or tokens.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;

    inserted := NULL;
    p := md5('knowledge-agent-v3:prompts:code-project-overview:' || workspace_id::text)::uuid;
    v := md5('knowledge-agent-v3:prompts:code-project-overview:v1:' || workspace_id::text)::uuid;
    INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
    VALUES(p,workspace_id,'Code project overview','Read one code project''s repository and draft its overview document for review.',
           'code',ARRAY['code-project-discovery','overview'],2)
    ON CONFLICT DO NOTHING RETURNING id INTO inserted;
    IF inserted IS NOT NULL THEN
        INSERT INTO documentation_prompt_versions(id,prompt_id,version,content,activated_at)
        VALUES(v,p,1,$prompt$Read the code project in the Resource line and draft an overview document for it.

1. Read what is already recorded first: get_resource for the code project, then list_documents for it. When it already has an overview document, read it with get_document and revise it with update_document instead of writing a new one.
2. Read the repository with the file tools (list_files, read_file, search_files) and code_graph: the folder structure, the entry points, the build and dependency files (for example package.json, pyproject.toml, pom.xml, go.mod, Makefile), the configuration files and environment variables, and the deployment files (for example Dockerfile, docker-compose files, Kubernetes manifests, CI pipelines). Read only; never change, install or delete anything.
3. Never copy passwords, keys, tokens or other secrets into the document. Name the setting and say what it is for instead.
4. Write the overview with new_document for the code project (or update_document of the existing overview), with these sections:
   - Purpose: what the code does and who uses it.
   - Structure: the main folders and modules and what each holds.
   - How to run and build: the commands to install, build, test and run it locally.
   - Configuration: the configuration files and environment variables, and what each controls.
   - Dependencies: the main libraries and the services it talks to (databases, queues, other APIs).
   - Deployment: how it is packaged and deployed, from its deployment files.
   - Where to look first: the files a new developer should open first, and why.
5. Say what you checked; mark anything you could not confirm from the code as unconfirmed.
6. Finish with a short report: what you wrote and what you could not check. The document is a draft a person reviews.$prompt$,now());
        UPDATE documentation_prompts SET active_version_id=v WHERE id=p;
    END IF;
END $seed$;
CREATE FUNCTION seed_code_project_prompts_on_project() RETURNS trigger
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $$
BEGIN
    PERFORM seed_code_project_prompts(NEW.id);
    RETURN NEW;
END $$;
CREATE TRIGGER seed_code_project_prompts AFTER INSERT ON projects
    FOR EACH ROW EXECUTE FUNCTION seed_code_project_prompts_on_project();
SELECT seed_code_project_prompts(id) FROM projects;

COMMIT;
