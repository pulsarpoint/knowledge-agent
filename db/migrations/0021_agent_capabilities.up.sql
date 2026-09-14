SET search_path = ka, public;

ALTER TABLE agents
    ADD COLUMN documents_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN repositories_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN ssh_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN skills_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN mcp_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN services_enabled boolean NOT NULL DEFAULT false;
-- Preserve each existing profile's access without granting additional capabilities.
UPDATE agents SET documents_enabled=(kind='search'), repositories_enabled=(kind IN ('search','code')),
    ssh_enabled=(kind='operations'), skills_enabled=true, mcp_enabled=(kind IN ('code','operations')), services_enabled=true;
ALTER TABLE agents DROP COLUMN kind;
COMMENT ON TABLE agents IS 'Named agents with independently configurable capabilities; no fixed execution types.';
