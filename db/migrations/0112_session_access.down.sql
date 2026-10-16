BEGIN;
SET search_path = ka, public;

UPDATE agent_sessions SET agent_config = agent_config - 'access' WHERE agent_config ? 'access';
ALTER TABLE agent_sessions DROP COLUMN access;

COMMIT;
