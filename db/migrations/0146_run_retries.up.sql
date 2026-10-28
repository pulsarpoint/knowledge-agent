BEGIN;
SET search_path = ka, public;

-- Run retries: an event run whose turn fails on a transient model error (a connection error, a timeout, 429, 5xx) is
-- retried up to 3 times. While it waits the run is 'retrying' (not final): its turn's inputs are queued again with a
-- not_before time and run as the same turn when due. `retries` counts the automatic retries of the latest turn;
-- `retried_at` is when a person last pressed Retry (an event run's first turn then gets a notification for the outcome
-- of that retry too). A failed Jira-started run posts a short failure comment: notifications.jira_text, posted in
-- place of the body.
ALTER TABLE agent_sessions DROP CONSTRAINT agent_sessions_status_check;
ALTER TABLE agent_sessions ADD CONSTRAINT agent_sessions_status_check
    CHECK (status IN ('starting','working','waiting','retrying','stopped','failed','closed'));
ALTER TABLE agent_sessions
    ADD COLUMN retries integer NOT NULL DEFAULT 0 CONSTRAINT agent_sessions_retries_check CHECK (retries >= 0),
    ADD COLUMN retried_at timestamptz;
COMMENT ON COLUMN agent_sessions.retries IS
    'Automatic retries of the latest turn so far (event runs, transient model errors; at most 3). A new turn starts at 0.';
COMMENT ON COLUMN agent_sessions.retried_at IS
    'When a person last pressed Retry on a failed turn (null: never).';

ALTER TABLE notifications ADD COLUMN jira_text text
    CONSTRAINT notifications_jira_text_check CHECK (char_length(jira_text) <= 200000);
COMMENT ON COLUMN notifications.jira_text IS
    'The Jira comment when it is not the body: a failed run''s short failure comment (null: the body is posted).';

COMMIT;
