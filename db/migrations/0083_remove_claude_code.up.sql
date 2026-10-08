BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Claude Code is gone: its sessions are deleted (inputs and reviews cascade; sessions point to documents, so saved
-- documents stay).
DELETE FROM agent_sessions_cc WHERE runtime = 'claude_code';

-- Claude Code agents become native with the tools their capabilities give, as a switch to native did
-- (knowledge_admin.tools.catalog.enabled_capability_tools; per run and fixed give the same tools here).
INSERT INTO agent_tools(agent_id, tool)
SELECT a.id, t.tool
FROM agents a
CROSS JOIN LATERAL (VALUES
    ('list_files',         a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('read_file',          a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('search_files',       a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('shell_run',          a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('code_search',        a.repositories_enabled),
    ('ast_grep',           a.repositories_enabled),
    ('code_graph',         a.repositories_enabled),
    ('search_documents',   a.documents_enabled),
    ('read_document',      a.documents_enabled),
    ('report_not_covered', a.documents_enabled AND a.documents_grounded),
    ('ssh_run',            a.ssh_enabled)
) AS t(tool, offered)
WHERE a.runtime = 'claude_code' AND t.offered
  AND NOT EXISTS (SELECT 1 FROM agent_tools x WHERE x.agent_id = a.id);

-- Every agent is native: the type and the Claude Code tools go (their checks go with them).
ALTER TABLE agents DROP COLUMN runtime, DROP COLUMN claude_code_tools;

-- Every session is native and pins its config.
ALTER TABLE agent_sessions_cc
    DROP CONSTRAINT agent_sessions_cc_ad_hoc,
    DROP CONSTRAINT agent_sessions_cc_native_config,
    DROP CONSTRAINT agent_sessions_cc_test_native,
    DROP CONSTRAINT agent_sessions_cc_runtime,
    DROP COLUMN runtime,
    DROP COLUMN claude_session_id,
    DROP COLUMN cost_usd,
    ALTER COLUMN agent_config SET NOT NULL;

ALTER TABLE agent_sessions_cc RENAME TO agent_sessions;
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'ka.agent_sessions'::regclass AND conname LIKE 'agent\_sessions\_cc\_%' LOOP
        EXECUTE format('ALTER TABLE ka.agent_sessions RENAME CONSTRAINT %I TO %I',
                       r.conname, 'agent_sessions_' || substr(r.conname, 19));
    END LOOP;
    FOR r IN SELECT indexname FROM pg_indexes
             WHERE schemaname = 'ka' AND tablename = 'agent_sessions' AND indexname LIKE 'agent\_sessions\_cc\_%' LOOP
        EXECUTE format('ALTER INDEX ka.%I RENAME TO %I', r.indexname, 'agent_sessions_' || substr(r.indexname, 19));
    END LOOP;
END $$;

ALTER TABLE agent_sessions
    ADD CONSTRAINT agent_sessions_ad_hoc CHECK (agent_id IS NOT NULL OR purpose = 'documents');

COMMIT;
