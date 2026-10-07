BEGIN;
SET search_path = ka, public;

-- An agent works with fixed resources (its own selections, as before) or receives them per run: its selections are
-- then what a caller may pass, and each run says what to work on. The coordinator always works with fixed resources.
ALTER TABLE agents
    ADD COLUMN resources text NOT NULL DEFAULT 'fixed'
        CONSTRAINT agent_resources_known CHECK (resources IN ('fixed','per_run')),
    ADD CONSTRAINT per_run_agent_is_not_the_coordinator CHECK (resources = 'fixed' OR NOT is_coordinator);
COMMENT ON COLUMN agents.resources IS
    'fixed: the agent always uses its selections. per_run: its selections are what a run may be given; each run passes its own.';

COMMIT;
