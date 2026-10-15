BEGIN;
SET search_path = ka, public;

-- 0110: a Send-to-agent handler may post its agent's answer as a comment on the Jira ticket that triggered it.
-- The notification is still written; it carries the comment's delivery state, which repository-worker
-- (events/jira_replies.py) moves from pending to posted or failed.
ALTER TABLE event_handlers ADD COLUMN reply_to_jira boolean NOT NULL DEFAULT false;
ALTER TABLE event_handlers ADD CONSTRAINT event_handlers_reply_to_jira CHECK (NOT reply_to_jira OR action = 'agent');

ALTER TABLE notifications
    ADD COLUMN jira_status text CHECK (jira_status IN ('pending', 'posted', 'failed')),
    ADD COLUMN jira_connection_id uuid,
    ADD COLUMN jira_issue_id text,
    ADD COLUMN jira_issue_key text,
    ADD COLUMN jira_comment_id text,
    ADD COLUMN jira_comment_created timestamptz,
    ADD COLUMN jira_attempts integer NOT NULL DEFAULT 0 CHECK (jira_attempts >= 0),
    ADD COLUMN jira_next_attempt_at timestamptz,
    ADD COLUMN jira_error text CHECK (char_length(jira_error) <= 2000),
    ADD CONSTRAINT notifications_jira_pending
        CHECK ((jira_status IS NOT DISTINCT FROM 'pending') = (jira_next_attempt_at IS NOT NULL)),
    ADD CONSTRAINT notifications_jira_posted
        CHECK ((jira_status IS NOT DISTINCT FROM 'posted') = (jira_comment_id IS NOT NULL AND jira_comment_created IS NOT NULL)),
    ADD CONSTRAINT notifications_jira_target
        CHECK (jira_status NOT IN ('pending', 'posted') OR (jira_connection_id IS NOT NULL AND jira_issue_id IS NOT NULL));
COMMENT ON COLUMN notifications.jira_connection_id IS 'The Jira connection that recorded the event (no foreign key: a deleted connection fails the delivery).';
COMMENT ON COLUMN notifications.jira_issue_id IS 'Jira issue id (not the key, which changes when the ticket moves).';

CREATE INDEX notifications_jira_due ON notifications(jira_next_attempt_at, id) WHERE jira_status = 'pending';
CREATE INDEX notifications_jira_posted ON notifications(jira_connection_id, jira_issue_id) WHERE jira_status = 'posted';

COMMIT;
