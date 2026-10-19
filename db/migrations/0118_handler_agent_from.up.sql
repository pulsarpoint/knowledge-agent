BEGIN;
SET search_path = ka, public;

-- 0118: a Send-to-agent handler's target is a fixed agent (agent_id) or the agent mapped to the Jira ticket's assignee
-- (ka.jira_user_agents by the event's connection and payload.assignee_id; agent_id is then null).
ALTER TABLE event_handlers ADD COLUMN agent_from text NOT NULL DEFAULT 'fixed' CHECK (agent_from IN ('fixed', 'assignee'));
ALTER TABLE event_handlers
    DROP CONSTRAINT event_handlers_action_fields,
    ADD CONSTRAINT event_handlers_action_fields CHECK (CASE action
        WHEN 'emit' THEN emit IS NOT NULL AND agent_id IS NULL AND prompt IS NULL AND agent_from = 'fixed'
        ELSE emit IS NULL AND prompt IS NOT NULL AND (agent_id IS NOT NULL) = (agent_from = 'fixed') END);
COMMENT ON COLUMN event_handlers.agent_from IS
    'Send to agent: fixed runs agent_id; assignee runs the agent mapped to the ticket''s Jira assignee (agent_id null).';

COMMIT;
