SET search_path = ka, public;

CREATE TABLE agents (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name text NOT NULL UNIQUE CHECK (char_length(btrim(name)) BETWEEN 1 AND 100),
    description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 4000),
    when_to_use text NOT NULL DEFAULT '' CHECK (char_length(when_to_use) <= 4000),
    kind text NOT NULL CHECK (kind IN ('search','code','operations')),
    enabled boolean NOT NULL DEFAULT true,
    model_id uuid REFERENCES llm_models(id),
    all_folders boolean NOT NULL DEFAULT false,
    all_repositories boolean NOT NULL DEFAULT false,
    include_subfolders boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
    created_by uuid REFERENCES principals(id),
    updated_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO agents(id,name,description,kind,model_id,all_folders,all_repositories)
    SELECT 'search','Search agent','Answer questions using documentation and source code.','search',search_model_id,true,true FROM llm_assignments
    UNION ALL SELECT 'code','Code search agent','Investigate questions in source repositories.','code',code_model_id,false,true FROM llm_assignments
    UNION ALL SELECT 'operations','SSH agent','Inspect and operate selected servers.','operations',operations_model_id,false,false FROM llm_assignments;
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agents FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Keep the legacy default-agent assignment API backed by the new canonical rows.
ALTER TABLE llm_assignments RENAME TO llm_assignment_revision;
ALTER TABLE llm_assignment_revision DROP COLUMN search_model_id, DROP COLUMN code_model_id, DROP COLUMN operations_model_id;
CREATE VIEW llm_assignments AS SELECT r.singleton,s.model_id AS search_model_id,c.model_id AS code_model_id,
    o.model_id AS operations_model_id,r.revision,r.updated_by,r.updated_at
    FROM llm_assignment_revision r CROSS JOIN agents s CROSS JOIN agents c CROSS JOIN agents o
    WHERE s.id='search' AND c.id='code' AND o.id='operations';
CREATE FUNCTION update_default_agent_models() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE ka.llm_assignment_revision SET revision=NEW.revision,updated_by=NEW.updated_by WHERE singleton;
    UPDATE ka.agents SET model_id=NEW.search_model_id,revision=revision+1,updated_by=NEW.updated_by WHERE id='search' AND model_id IS DISTINCT FROM NEW.search_model_id;
    UPDATE ka.agents SET model_id=NEW.code_model_id,revision=revision+1,updated_by=NEW.updated_by WHERE id='code' AND model_id IS DISTINCT FROM NEW.code_model_id;
    UPDATE ka.agents SET model_id=NEW.operations_model_id,revision=revision+1,updated_by=NEW.updated_by WHERE id='operations' AND model_id IS DISTINCT FROM NEW.operations_model_id;
    RETURN NEW;
END;
$$;
CREATE TRIGGER update_default_agent_models INSTEAD OF UPDATE ON llm_assignments FOR EACH ROW EXECUTE FUNCTION update_default_agent_models();

SET CONSTRAINTS ka.active_prompt_version_fk IMMEDIATE;
ALTER TABLE agent_prompts DROP CONSTRAINT agent_prompts_agent_check;
ALTER TABLE agent_prompts ADD FOREIGN KEY(agent) REFERENCES agents(id);
SET CONSTRAINTS ka.active_prompt_version_fk DEFERRED;
ALTER TABLE agent_configs DROP CONSTRAINT agent_configs_agent_check;
ALTER TABLE agent_configs DROP CONSTRAINT agent_configs_check1;
ALTER TABLE agent_configs ADD FOREIGN KEY(agent) REFERENCES agents(id);

ALTER TABLE operations_servers DROP CONSTRAINT operations_servers_project_id_fkey;
ALTER TABLE operations_settings DROP CONSTRAINT operations_settings_pkey;
ALTER TABLE operations_settings ADD COLUMN agent text NOT NULL DEFAULT 'operations' REFERENCES agents(id);
ALTER TABLE operations_settings ADD PRIMARY KEY(project_id,agent);
ALTER TABLE operations_servers DROP CONSTRAINT operations_servers_pkey;
ALTER TABLE operations_servers ADD COLUMN agent text NOT NULL DEFAULT 'operations' REFERENCES agents(id);
ALTER TABLE operations_servers ADD PRIMARY KEY(project_id,agent,server_id);
ALTER TABLE operations_servers ADD FOREIGN KEY(project_id,agent) REFERENCES operations_settings(project_id,agent);
ALTER TABLE operations_runs ADD COLUMN agent text NOT NULL DEFAULT 'operations' REFERENCES agents(id);
CREATE INDEX operations_runs_agent_recent ON operations_runs(agent,created_at DESC);

CREATE TABLE agent_knowledge_folders (
    agent_id text NOT NULL REFERENCES agents(id), folder_id uuid NOT NULL REFERENCES rag_folders(id) ON DELETE CASCADE,
    PRIMARY KEY(agent_id,folder_id)
);
CREATE TABLE agent_repositories (
    agent_id text NOT NULL REFERENCES agents(id), repository_id uuid NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    PRIMARY KEY(agent_id,repository_id)
);
CREATE TABLE agent_services (
    agent_id text NOT NULL REFERENCES agents(id), service_id uuid NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    PRIMARY KEY(agent_id,service_id)
);
COMMENT ON TABLE agents IS 'Named agent profiles. Fixed default IDs preserve existing reader and admin integrations; custom profiles use UUID text IDs.';
