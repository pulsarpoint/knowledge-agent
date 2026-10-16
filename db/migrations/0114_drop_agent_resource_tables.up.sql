BEGIN;
SET search_path = ka, public;

-- A saved agent's access object (0113) is the only record of what it may use: the per-type selection tables, the
-- per-server overrides, the per-agent SSH connections and the per-kind switches go.
DROP TABLE agent_config_mcp_servers, agent_config_skills, agent_configs, agent_server_connections, operations_servers,
    operations_settings, skills_agents, agent_services, agent_collections, agent_repositories;

ALTER TABLE agents
    DROP COLUMN documents_enabled,
    DROP COLUMN repositories_enabled,
    DROP COLUMN ssh_enabled,
    DROP COLUMN skills_enabled,
    DROP COLUMN mcp_enabled,
    DROP COLUMN services_enabled,
    DROP COLUMN all_collections,
    DROP COLUMN all_repositories,
    DROP COLUMN resources;

COMMIT;
