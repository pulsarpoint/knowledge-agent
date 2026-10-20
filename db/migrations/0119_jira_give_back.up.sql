BEGIN;
SET search_path = ka, public;

-- 0119: a handler that replies in Jira for the assignee's agent may give the ticket back after the reply:
-- repository-worker (events/jira_replies.py) assigns it to the person who gave it to the agent (the previous assignee,
-- else the reporter, never a mapped Jira user; chosen when the notification is written) once the comment is posted,
-- or skips it when someone else took the ticket meanwhile.
ALTER TABLE event_handlers ADD COLUMN give_back boolean NOT NULL DEFAULT false;
ALTER TABLE event_handlers ADD CONSTRAINT event_handlers_give_back CHECK (NOT give_back OR (reply_to_jira AND agent_from = 'assignee'));

ALTER TABLE notifications
    ADD COLUMN give_back_key text,
    ADD COLUMN give_back_name text,
    ADD COLUMN give_back_status text CHECK (give_back_status IN ('pending', 'done', 'failed', 'skipped')),
    ADD COLUMN give_back_error text CHECK (char_length(give_back_error) <= 2000),
    ADD COLUMN give_back_attempts integer NOT NULL DEFAULT 0 CHECK (give_back_attempts >= 0),
    ADD COLUMN give_back_next_attempt_at timestamptz,
    ADD CONSTRAINT notifications_give_back_target CHECK ((give_back_status IS NULL) = (give_back_key IS NULL)),
    ADD CONSTRAINT notifications_give_back_after_comment
        CHECK (give_back_next_attempt_at IS NULL OR (give_back_status = 'pending' AND jira_status = 'posted'));
COMMENT ON COLUMN notifications.give_back_key IS 'Jira user key the ticket is given back to after the reply is posted.';
COMMENT ON COLUMN notifications.give_back_next_attempt_at IS 'When the assignment is due: set once the comment is posted.';

CREATE INDEX notifications_give_back_due ON notifications(give_back_next_attempt_at, id) WHERE give_back_status = 'pending';

COMMIT;
