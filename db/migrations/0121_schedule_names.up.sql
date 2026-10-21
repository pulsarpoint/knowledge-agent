BEGIN;
SET search_path = ka, public;

-- 0121: a handler binds to an emit schedule by its name (the schedule.fired event's subject), so names are unique per
-- project. A repeated name gets " (2)", " (3)"... (the oldest keeps it), skipping names already taken.
DO $$
DECLARE
    duplicate record;
    number int;
BEGIN
    FOR duplicate IN
        SELECT id, project_id, name FROM (
            SELECT id, project_id, name,
                row_number() OVER (PARTITION BY project_id, name ORDER BY created_at, id) AS position
            FROM schedules
        ) ranked WHERE position > 1 ORDER BY project_id, name, position
    LOOP
        number := 2;
        WHILE EXISTS (
            SELECT 1 FROM schedules
            WHERE project_id = duplicate.project_id AND name = duplicate.name || ' (' || number || ')'
        ) LOOP
            number := number + 1;
        END LOOP;
        UPDATE schedules SET name = duplicate.name || ' (' || number || ')' WHERE id = duplicate.id;
    END LOOP;
END $$;
ALTER TABLE schedules ADD CONSTRAINT schedules_project_name_key UNIQUE (project_id, name);

-- Handlers bound by payload.schedule_id equals <id> now check subject equals <that schedule's name>. The patterns are
-- every type pattern that matches schedule.fired (matching.type_matches).
UPDATE event_handlers h SET
    conditions = (
        SELECT jsonb_agg(
            CASE WHEN c ->> 'field' = 'payload.schedule_id' AND c ->> 'op' = 'equals' AND s.name IS NOT NULL
                THEN jsonb_build_object('field', 'subject', 'op', 'equals', 'value', s.name)
                ELSE c END
            ORDER BY position)
        FROM jsonb_array_elements(h.conditions) WITH ORDINALITY AS item(c, position)
        LEFT JOIN schedules s ON s.project_id = h.project_id AND s.target_kind = 'event.emit'
            AND s.id::text = item.c ->> 'value'
    ),
    revision = revision + 1,
    updated_at = now()
WHERE h.type_pattern IN ('*', '*.*', 'schedule.*', '*.fired', 'schedule.fired')
    AND EXISTS (
        SELECT 1 FROM jsonb_array_elements(h.conditions) c
        JOIN schedules s ON s.project_id = h.project_id AND s.target_kind = 'event.emit' AND s.id::text = c ->> 'value'
        WHERE c ->> 'field' = 'payload.schedule_id' AND c ->> 'op' = 'equals'
    );

COMMIT;
