SET search_path = ka, public;

DROP TABLE reader_settings;
DROP INDEX agent_sessions_reader_owner;
ALTER TABLE agent_sessions DROP COLUMN channel, DROP COLUMN hidden_by_owner_at, DROP COLUMN idle_timeout;
ALTER TABLE agents DROP COLUMN documents_grounded;
