BEGIN;
SET search_path = ka, public;

-- 0116: a run's access object (agent_sessions.access, 0112) is the only description of what it may use; what the
-- person chose is its entries given (source 'given'). The ids-only record of 0076 goes.
ALTER TABLE agent_sessions DROP COLUMN run_resources;

COMMIT;
