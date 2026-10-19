BEGIN;
SET search_path = ka, public;

DELETE FROM event_handlers WHERE agent_from = 'assignee';
ALTER TABLE event_handlers
    DROP CONSTRAINT event_handlers_action_fields,
    ADD CONSTRAINT event_handlers_action_fields CHECK (CASE action
        WHEN 'emit' THEN emit IS NOT NULL AND agent_id IS NULL AND prompt IS NULL
        ELSE emit IS NULL AND agent_id IS NOT NULL AND prompt IS NOT NULL END);
ALTER TABLE event_handlers DROP COLUMN agent_from;

COMMIT;
