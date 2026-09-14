SET search_path = ka, public;

CREATE TABLE agent_configs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    agent text NOT NULL CHECK (agent IN ('search', 'code', 'operations')),
    server_id uuid,
    instructions text CHECK (char_length(instructions) <= 40000),
    inherit_skills boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_by uuid REFERENCES principals(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (project_id, agent, server_id),
    UNIQUE (id, project_id),
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    CHECK (server_id IS NOT NULL OR (instructions IS NOT NULL AND NOT inherit_skills)),
    CHECK (server_id IS NULL OR agent = 'operations')
);
CREATE TABLE agent_config_skills (
    config_id uuid NOT NULL,
    project_id uuid NOT NULL,
    skill_id uuid NOT NULL,
    PRIMARY KEY (config_id, skill_id),
    FOREIGN KEY (config_id, project_id) REFERENCES agent_configs(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (skill_id, project_id) REFERENCES agent_skills(id, project_id) ON DELETE CASCADE
);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_configs
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Preserve the existing global defaults and explicit server runbooks.
INSERT INTO agent_configs(project_id, agent, instructions, inherit_skills, updated_by)
    SELECT id, 'operations', coalesce(s.instructions, ''), false, s.updated_by
    FROM projects p LEFT JOIN operations_settings s ON s.project_id=p.id;
INSERT INTO agent_config_skills(config_id, project_id, skill_id)
    SELECT c.id, s.project_id, s.skill_id FROM operations_skills s
    JOIN agent_configs c ON c.project_id=s.project_id AND c.agent='operations' AND c.server_id IS NULL;
INSERT INTO agent_configs(project_id, agent, server_id, instructions)
    SELECT project_id, 'operations', id, operation_instructions FROM servers WHERE operation_instructions <> '';

DROP TABLE operations_skills;
ALTER TABLE operations_settings DROP COLUMN instructions;
ALTER TABLE servers DROP COLUMN operation_instructions;
