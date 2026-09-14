SET search_path = ka, public;

CREATE TABLE skills_agents (
    agent_id text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    skill_id uuid NOT NULL,
    project_id uuid NOT NULL,
    PRIMARY KEY (agent_id, skill_id),
    FOREIGN KEY (skill_id, project_id) REFERENCES agent_skills(id, project_id) ON DELETE CASCADE
);
CREATE INDEX skills_agents_skill_id ON skills_agents(skill_id);

INSERT INTO skills_agents(agent_id, skill_id, project_id)
    SELECT c.agent, s.skill_id, s.project_id
    FROM agent_config_skills s JOIN agent_configs c ON c.id=s.config_id
    WHERE c.server_id IS NULL;

DELETE FROM agent_config_skills s USING agent_configs c
    WHERE c.id=s.config_id AND c.server_id IS NULL;

COMMENT ON TABLE skills_agents IS 'Skills selected for each agent. Disabling skills preserves these assignments.';
COMMENT ON TABLE agent_config_skills IS 'Explicit skill selections for server-specific agent configurations.';
