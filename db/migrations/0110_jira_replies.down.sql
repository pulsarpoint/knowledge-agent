BEGIN;
SET search_path = ka, public;

DROP INDEX notifications_jira_posted;
DROP INDEX notifications_jira_due;
ALTER TABLE notifications
    DROP COLUMN jira_status,
    DROP COLUMN jira_connection_id,
    DROP COLUMN jira_issue_id,
    DROP COLUMN jira_issue_key,
    DROP COLUMN jira_comment_id,
    DROP COLUMN jira_comment_created,
    DROP COLUMN jira_attempts,
    DROP COLUMN jira_next_attempt_at,
    DROP COLUMN jira_error;
ALTER TABLE event_handlers DROP COLUMN reply_to_jira;

COMMIT;
