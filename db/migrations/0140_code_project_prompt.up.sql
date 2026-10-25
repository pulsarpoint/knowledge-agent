BEGIN;
SET search_path = ka, public;

-- The built-in "Code project overview" prompt: a run started from a code project reads the repository and drafts an
-- overview document for it. Tagged code-project, so a chat on a code project and its Discover wizard offer it. Every
-- workspace gets it once (seeded like Server discovery, 0097): the existing ones now, new ones on creation.
CREATE FUNCTION seed_code_project_overview_prompt(workspace_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $seed$
DECLARE p uuid; v uuid; inserted uuid;
BEGIN
    p := md5('knowledge-agent-v3:prompts:code-project-overview:' || workspace_id::text)::uuid;
    v := md5('knowledge-agent-v3:prompts:code-project-overview:v1:' || workspace_id::text)::uuid;
    INSERT INTO documentation_prompts(id,project_id,name,description,input_kind,tags,next_version)
    VALUES(p,workspace_id,'Code project overview','Read one code project''s repository and draft its overview document for review.',
           'code',ARRAY['code-project','overview'],2)
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
CREATE FUNCTION seed_code_project_overview_on_project() RETURNS trigger
LANGUAGE plpgsql SET search_path=ka,pg_temp AS $$
BEGIN
    PERFORM seed_code_project_overview_prompt(NEW.id);
    RETURN NEW;
END $$;
CREATE TRIGGER seed_code_project_overview AFTER INSERT ON projects
    FOR EACH ROW EXECUTE FUNCTION seed_code_project_overview_on_project();
SELECT seed_code_project_overview_prompt(id) FROM projects;

COMMIT;
