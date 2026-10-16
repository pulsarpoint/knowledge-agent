BEGIN;
SET search_path = ka, public;

-- Every run's access object: the resources it may use ({type, id, name, description_short, source}, source 'given' or
-- 'via <type> <id>'; a document may be editable) and its roles. Pinned at start; also in agent_config as 'access'.
ALTER TABLE agent_sessions
    ADD COLUMN access jsonb NOT NULL DEFAULT '{"resources": [], "roles": {"content_editor": false}}',
    ADD CONSTRAINT agent_sessions_access CHECK (jsonb_typeof(access->'resources') = 'array'
                                                AND jsonb_typeof(access->'roles') = 'object');
COMMENT ON COLUMN agent_sessions.access IS
    'The run''s access object: resources (given or via a given parent) and roles (content_editor); fixed at start.';

CREATE OR REPLACE FUNCTION pg_temp.short(text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN length(btrim(coalesce($1, ''))) <= 300 THEN btrim(coalesce($1, ''))
                ELSE rtrim(left(btrim($1), 300)) || '…' END
$$;

-- Existing runs: what they were given (run_resources, chosen environments, document_access), all marked given; the
-- runs that wrote (a new document, a revision, proposals) are content editors.
UPDATE agent_sessions s SET access = jsonb_build_object(
    'resources', coalesce((
        SELECT jsonb_agg(item ORDER BY kind, position) FROM (
            SELECT 1 AS kind, x.position, jsonb_build_object('type', 'code_project', 'id', x.id,
                   'name', coalesce(r.name, x.id), 'description_short', pg_temp.short(r.description), 'source', 'given') AS item
            FROM jsonb_array_elements_text(s.run_resources->'code_project_ids') WITH ORDINALITY AS x(id, position)
            LEFT JOIN repositories r ON r.id::text = x.id
            UNION ALL
            SELECT 2, x.position, jsonb_build_object('type', 'collection', 'id', x.id,
                   'name', coalesce(c.name, x.id), 'description_short', pg_temp.short(c.description), 'source', 'given')
            FROM jsonb_array_elements_text(s.run_resources->'collection_ids') WITH ORDINALITY AS x(id, position)
            LEFT JOIN rag_collections c ON c.id::text = x.id
            UNION ALL
            SELECT 3, x.position, jsonb_build_object('type', 'server', 'id', x.id,
                   'name', coalesce(v.name, x.id), 'description_short', pg_temp.short(v.description), 'source', 'given')
            FROM jsonb_array_elements_text(s.run_resources->'server_ids') WITH ORDINALITY AS x(id, position)
            LEFT JOIN servers v ON v.id::text = x.id
            UNION ALL
            SELECT 4, x.position, jsonb_build_object('type', 'service', 'id', x.id,
                   'name', coalesce(v.name, x.id), 'description_short', pg_temp.short(v.description), 'source', 'given')
            FROM jsonb_array_elements_text(s.run_resources->'service_ids') WITH ORDINALITY AS x(id, position)
            LEFT JOIN services v ON v.id::text = x.id
            UNION ALL
            SELECT 5, x.position, jsonb_build_object('type', 'environment', 'id', x.id,
                   'name', coalesce(e.name, x.id), 'description_short', pg_temp.short(e.description), 'source', 'given')
            FROM jsonb_array_elements_text(s.agent_config->'chosen_resources'->'environment_ids')
                 WITH ORDINALITY AS x(id, position)
            LEFT JOIN environments e ON e.id::text = x.id
            UNION ALL
            SELECT 6, x.position, jsonb_build_object('type', 'document', 'id', x.d->>'id', 'name', x.d->>'title',
                   'description_short', '', 'source', 'given', 'editable', coalesce((x.d->>'editable')::boolean, false))
            FROM jsonb_array_elements(CASE WHEN jsonb_typeof(s.agent_config->'document_access') = 'array'
                                           THEN s.agent_config->'document_access' ELSE '[]' END)
                 WITH ORDINALITY AS x(d, position)
        ) given), '[]'::jsonb),
    'roles', jsonb_build_object('content_editor', COALESCE(
        s.agent_id IS NULL AND (s.purpose IN ('documents', 'revision')
                                OR jsonb_typeof(s.agent_config->'proposal_tools') = 'array'
                                   AND jsonb_array_length(s.agent_config->'proposal_tools') > 0), false)));
UPDATE agent_sessions SET agent_config = agent_config || jsonb_build_object('access', access) WHERE agent_config IS NOT NULL;

COMMIT;
