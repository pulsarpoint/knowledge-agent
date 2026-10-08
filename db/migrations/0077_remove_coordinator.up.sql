BEGIN;
SET search_path = ka, public;

-- The multi-agent board and its coordinator are removed; orchestration comes back later as agents calling agents
-- through tools. Board tasks, their member rows and run records (board:<task id>) go, the coordinator settings go,
-- and the coordinator agent goes with everything it owns, as deleting an agent with its history does. Audit events
-- stay, as for every deleted agent.
DELETE FROM agent_run_events WHERE starts_with(run_key, 'board:');
DROP TABLE agent_board_members;
DROP TABLE agent_board_tasks;
DROP TABLE coordinator_settings;

DROP TRIGGER protect_coordinator_agent ON agents;
DROP FUNCTION protect_coordinator_agent();

DELETE FROM agent_run_events e USING operations_runs r, agents a
    WHERE e.run_key = 'run:' || r.id AND r.agent = a.id AND a.is_coordinator;
DELETE FROM agent_run_events e USING agent_sessions_cc s, agents a
    WHERE starts_with(e.run_key, 'cc:') AND split_part(e.run_key, ':', 2) = s.id::text
      AND s.agent_id = a.id AND a.is_coordinator;
DELETE FROM operations_commands c USING operations_runs r, agents a
    WHERE c.run_id = r.id AND r.agent = a.id AND a.is_coordinator;
DELETE FROM operations_runs r USING agents a WHERE r.agent = a.id AND a.is_coordinator;
UPDATE agent_versions v SET source_review_id = NULL FROM agent_session_reviews w, agent_sessions s, agents a
    WHERE v.source_review_id = w.id AND w.session_id = s.id AND s.agent = a.id AND a.is_coordinator;
DELETE FROM agent_session_reviews w USING agent_sessions s, agents a
    WHERE w.session_id = s.id AND s.agent = a.id AND a.is_coordinator;
DELETE FROM agent_sessions s USING agents a WHERE s.agent = a.id AND a.is_coordinator;
DELETE FROM agent_sessions_cc s USING agents a WHERE s.agent_id = a.id AND a.is_coordinator;  -- inputs cascade
DELETE FROM agent_server_connections c USING agents a WHERE c.agent_id = a.id AND a.is_coordinator;
DELETE FROM agent_collections c USING agents a WHERE c.agent_id = a.id AND a.is_coordinator;
DELETE FROM agent_repositories c USING agents a WHERE c.agent_id = a.id AND a.is_coordinator;
DELETE FROM agent_services c USING agents a WHERE c.agent_id = a.id AND a.is_coordinator;
DELETE FROM skills_agents c USING agents a WHERE c.agent_id = a.id AND a.is_coordinator;
DELETE FROM operations_servers s USING agents a WHERE s.agent = a.id AND a.is_coordinator;
DELETE FROM operations_settings s USING agents a WHERE s.agent = a.id AND a.is_coordinator;
DELETE FROM agent_configs c USING agents a WHERE c.agent = a.id AND a.is_coordinator;
-- Both sides of the required-prompt relationship are deferred.
DELETE FROM agent_prompts p USING agents a WHERE p.agent = a.id AND a.is_coordinator;
DELETE FROM agent_prompt_versions v USING agents a WHERE v.agent = a.id AND a.is_coordinator;
DELETE FROM agents WHERE is_coordinator;  -- its configuration versions cascade
-- Check the deleted rows now, so no deferred event is pending when agents is altered below.
SET CONSTRAINTS ALL IMMEDIATE;

ALTER TABLE agents
    DROP CONSTRAINT coordinator_delegates_resources,
    DROP CONSTRAINT coordinator_always_enabled,
    DROP CONSTRAINT claude_code_agent_is_not_the_coordinator,
    DROP CONSTRAINT per_run_agent_is_not_the_coordinator;
DROP INDEX one_coordinator_agent;
ALTER TABLE agents DROP COLUMN is_coordinator;

COMMIT;
