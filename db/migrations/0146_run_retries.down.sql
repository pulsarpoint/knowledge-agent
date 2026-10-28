BEGIN;
SET search_path = ka, public;

-- Back to 0145: no retries. A run waiting for a retry becomes failed (its queued inputs stay queued and are delivered
-- as the next turn).
ALTER TABLE notifications DROP COLUMN jira_text;
ALTER TABLE agent_sessions DROP COLUMN retried_at, DROP COLUMN retries;
UPDATE agent_sessions SET status = 'failed' WHERE status = 'retrying';
ALTER TABLE agent_sessions DROP CONSTRAINT agent_sessions_status_check;
ALTER TABLE agent_sessions ADD CONSTRAINT agent_sessions_status_check
    CHECK (status IN ('starting','working','waiting','stopped','failed','closed'));

COMMIT;
