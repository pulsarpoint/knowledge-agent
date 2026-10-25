BEGIN;
SET search_path = ka, public;

-- Back to 0140: nothing to restore. The removed skill access entries do not come back (skills are test data), and the
-- skills a run pinned in its agent_config stay where 0140 code ignores them.

COMMIT;
