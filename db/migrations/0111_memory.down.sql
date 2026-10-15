BEGIN;
SET search_path = ka, public;

DELETE FROM events WHERE source = 'memory';
ALTER TABLE events
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira', 'handler', 'manual'));
DELETE FROM tools WHERE tool_group = 'memory';
ALTER TABLE tools
    DROP CONSTRAINT tools_tool_group_check,
    ADD CONSTRAINT tools_tool_group_check
        CHECK (tool_group IN ('code', 'documents', 'servers', 'inventory', 'agents'));
DROP TABLE memory_versions;
DROP TABLE memories;
DROP FUNCTION jsonb_values_text(jsonb);

COMMIT;
