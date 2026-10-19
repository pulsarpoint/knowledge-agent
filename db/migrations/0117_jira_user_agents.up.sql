BEGIN;
SET search_path = ka, public;

-- 0117: Jira users → agents. A handler that targets the ticket's assignee runs the agent mapped to that Jira user.
-- Matching uses the Jira Data Center user key (stable); the name is the display name, shown only. One Jira user maps
-- to one agent per connection; an agent may have several Jira users.
CREATE TABLE jira_user_agents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id   uuid NOT NULL REFERENCES jira_connections(id) ON DELETE CASCADE,
    jira_user_key   text NOT NULL CHECK (char_length(btrim(jira_user_key)) BETWEEN 1 AND 255),
    jira_user_name  text NOT NULL CHECK (char_length(jira_user_name) <= 255),
    agent_id        text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    created_by      uuid REFERENCES principals(id),
    updated_by      uuid REFERENCES principals(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (connection_id, jira_user_key)
);
CREATE INDEX jira_user_agents_agent ON jira_user_agents(agent_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON jira_user_agents FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMIT;
