BEGIN;
SET search_path = ka, public;

-- 0111: memory. Agents save what they learn under a unique name with metadata for filtering and content found by
-- words (search_vector) and by meaning (embedding, made by the project's RAG embedding model; NULL when it could not
-- be made). Every change keeps the earlier revision in memory_versions and records a memory.* event.
CREATE FUNCTION jsonb_values_text(value jsonb) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN jsonb_typeof(value) = 'object'
        THEN (SELECT coalesce(string_agg(item.value, ' ' ORDER BY item.key), '') FROM jsonb_each_text(value) AS item)
        ELSE '' END
$$;

CREATE TABLE memories (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         uuid NOT NULL REFERENCES projects(id),
    name               text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9._/-]{0,199}$'),
    metadata           jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
    content            text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 20000),
    search_vector      tsvector GENERATED ALWAYS AS (to_tsvector('simple',
                           replace(name, '/', ' ') || ' ' || content || ' ' || jsonb_values_text(metadata))) STORED,
    embedding          public.vector,
    embedding_profile  text,
    revision           integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_by_agent   text REFERENCES agents(id) ON DELETE SET NULL,
    updated_by_agent   text REFERENCES agents(id) ON DELETE SET NULL,
    created_by_session uuid,
    updated_by_session uuid,
    created_by_actor   uuid REFERENCES principals(id),
    updated_by_actor   uuid REFERENCES principals(id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name),
    CONSTRAINT memories_embedding_profile CHECK ((embedding IS NULL) = (embedding_profile IS NULL))
);
COMMENT ON COLUMN memories.created_by_session IS 'ka.agent_sessions.id of the run that saved it (no foreign key: the trace outlives the session).';
CREATE INDEX memories_search ON memories USING gin(search_vector);
CREATE INDEX memories_metadata ON memories USING gin(metadata jsonb_path_ops);
CREATE INDEX memories_recent ON memories(project_id, updated_at DESC, id);

CREATE TABLE memory_versions (
    memory_id   uuid NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    revision    integer NOT NULL,
    name        text NOT NULL,
    metadata    jsonb NOT NULL,
    content     text NOT NULL,
    by_agent    text,
    by_session  uuid,
    by_actor    uuid,
    changed_at  timestamptz NOT NULL,
    PRIMARY KEY (memory_id, revision)
);
COMMENT ON TABLE memory_versions IS 'Earlier revisions of a memory: copied here before each update, restore or delete.';

ALTER TABLE tools
    DROP CONSTRAINT tools_tool_group_check,
    ADD CONSTRAINT tools_tool_group_check
        CHECK (tool_group IN ('code', 'documents', 'servers', 'inventory', 'agents', 'memory'));
ALTER TABLE events
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira', 'handler', 'manual', 'memory'));

COMMIT;
