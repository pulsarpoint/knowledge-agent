BEGIN;
SET search_path = ka, public;

-- Best effort (test data): handlers checking subject equals <an emit schedule's name> go back to
-- payload.schedule_id equals <its id>; names renamed by the up migration keep their suffix.
UPDATE event_handlers h SET
    conditions = (
        SELECT jsonb_agg(
            CASE WHEN c ->> 'field' = 'subject' AND c ->> 'op' = 'equals' AND s.id IS NOT NULL
                THEN jsonb_build_object('field', 'payload.schedule_id', 'op', 'equals', 'value', s.id::text)
                ELSE c END
            ORDER BY position)
        FROM jsonb_array_elements(h.conditions) WITH ORDINALITY AS item(c, position)
        LEFT JOIN schedules s ON s.project_id = h.project_id AND s.target_kind = 'event.emit'
            AND s.name = item.c ->> 'value'
    ),
    revision = revision + 1,
    updated_at = now()
WHERE h.type_pattern IN ('*', '*.*', 'schedule.*', '*.fired', 'schedule.fired')
    AND EXISTS (
        SELECT 1 FROM jsonb_array_elements(h.conditions) c
        JOIN schedules s ON s.project_id = h.project_id AND s.target_kind = 'event.emit' AND s.name = c ->> 'value'
        WHERE c ->> 'field' = 'subject' AND c ->> 'op' = 'equals'
    );
ALTER TABLE schedules DROP CONSTRAINT schedules_project_name_key;

COMMIT;
