SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agents a WHERE a.runtime = 'claude_code' AND (
            a.is_coordinator OR a.model_id IS NOT NULL OR a.active_config_version_id IS NOT NULL
            OR a.documents_enabled OR a.documents_grounded OR a.repositories_enabled OR a.ssh_enabled
            OR a.skills_enabled OR a.mcp_enabled OR a.services_enabled OR a.all_collections OR a.all_repositories
            OR a.claude_code_tools <> '{Read,Grep,Glob,Bash}'
            OR EXISTS (SELECT 1 FROM agent_versions v WHERE v.agent = a.id)
            OR (SELECT count(*) FROM agent_prompt_versions v WHERE v.agent = a.id) > 1
            OR EXISTS (SELECT 1 FROM agent_configs c WHERE c.agent = a.id
                       AND (c.server_id IS NOT NULL OR btrim(coalesce(c.instructions, '')) <> ''))
            OR EXISTS (SELECT 1 FROM agent_server_connections c WHERE c.agent_id = a.id)
            OR EXISTS (SELECT 1 FROM operations_servers s WHERE s.agent = a.id))) THEN
        RAISE EXCEPTION 'Claude Code agents have a configuration that 0070 cannot keep; delete them or reset them to a name and a description before rolling back 0072';
    END IF;
END $$;

-- Registrations keep only their name and description: their prompt and empty settings rows go.
ALTER TABLE agents DROP CONSTRAINT agent_system_prompt_fk;
DELETE FROM agent_configs c USING agents a WHERE c.agent = a.id AND a.runtime = 'claude_code';
DELETE FROM operations_settings s USING agents a WHERE s.agent = a.id AND a.runtime = 'claude_code';
DELETE FROM skills_agents s USING agents a WHERE s.agent_id = a.id AND a.runtime = 'claude_code';
DELETE FROM agent_collections s USING agents a WHERE s.agent_id = a.id AND a.runtime = 'claude_code';
DELETE FROM agent_repositories s USING agents a WHERE s.agent_id = a.id AND a.runtime = 'claude_code';
DELETE FROM agent_services s USING agents a WHERE s.agent_id = a.id AND a.runtime = 'claude_code';
DELETE FROM agent_prompts p USING agents a WHERE p.agent = a.id AND a.runtime = 'claude_code';
DELETE FROM agent_prompt_versions v USING agents a WHERE v.agent = a.id AND a.runtime = 'claude_code';
SET CONSTRAINTS ka.active_prompt_version_fk, ka.agent_prompt_versions_agent_fkey IMMEDIATE;
SET CONSTRAINTS ka.active_prompt_version_fk, ka.agent_prompt_versions_agent_fkey DEFERRED;

ALTER TABLE agents ADD COLUMN prompt_agent text GENERATED ALWAYS AS (CASE WHEN runtime = 'openai' THEN id END) STORED;
ALTER TABLE agents ADD CONSTRAINT agent_system_prompt_fk FOREIGN KEY (prompt_agent) REFERENCES agent_prompts(agent)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agents DROP CONSTRAINT claude_code_agent_is_not_the_coordinator;
ALTER TABLE agents DROP COLUMN claude_code_tools;
ALTER TABLE agents ADD CONSTRAINT claude_code_agent_is_a_registration CHECK (runtime = 'openai' OR (
    NOT is_coordinator AND model_id IS NULL AND active_config_version_id IS NULL
    AND NOT documents_enabled AND NOT documents_grounded AND NOT repositories_enabled AND NOT ssh_enabled
    AND NOT skills_enabled AND NOT mcp_enabled AND NOT services_enabled AND NOT all_collections AND NOT all_repositories));
