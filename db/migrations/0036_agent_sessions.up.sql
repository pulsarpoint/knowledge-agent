BEGIN;
ALTER TABLE ka.agent_test_sessions RENAME TO agent_sessions;
ALTER INDEX ka.agent_test_sessions_agent_created RENAME TO agent_sessions_agent_created;
ALTER TABLE ka.agent_sessions ADD COLUMN is_test boolean NOT NULL DEFAULT true;
ALTER TABLE ka.agent_sessions ALTER COLUMN is_test SET DEFAULT false;
ALTER TABLE ka.agent_sessions ADD COLUMN history jsonb NOT NULL DEFAULT '[]'
    CHECK (jsonb_typeof(history) = 'array');

-- Preserve the transcript for sessions that predate full SDK history storage.
-- The one-time file importer restores complete tool history where it exists.
UPDATE ka.agent_sessions s SET history = transcript.history
FROM (
    SELECT r.session_id, jsonb_agg(message.item ORDER BY r.created_at,r.id,message.position) AS history
    FROM ka.operations_runs r
    CROSS JOIN LATERAL (VALUES
        (0, jsonb_build_object('role','user','content',r.task)),
        (1, jsonb_build_object('role','assistant','content',r.answer))
    ) AS message(position,item)
    WHERE r.session_id IS NOT NULL AND r.status='completed'
    GROUP BY r.session_id
) transcript WHERE s.id=transcript.session_id;
COMMIT;
