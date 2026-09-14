BEGIN;
ALTER TABLE ka.agent_test_sessions DROP COLUMN agent_version_id;
ALTER TABLE ka.agents DROP COLUMN active_config_version_id;
ALTER TABLE ka.agents DROP COLUMN next_config_version;
DROP TABLE ka.agent_versions;
COMMIT;
