BEGIN;
SET search_path = ka, public;

-- Prompts are a library organised by tags (lowercase letters, digits and -, up to 40 characters, at most 10, no
-- duplicates). Features find their prompts by tag. input_kind is retired: the per-workspace seed functions still
-- insert it, so the column stays with a default, and untagged inserts are tagged from it.
CREATE FUNCTION documentation_prompt_tags_valid(tags text[]) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT NOT EXISTS (SELECT 1 FROM unnest(tags) AS tag WHERE tag IS NULL OR tag !~ '^[a-z0-9][a-z0-9-]{0,39}$')
        AND cardinality(tags) = (SELECT count(DISTINCT tag) FROM unnest(tags) AS tag)
$$;
ALTER TABLE documentation_prompts
    ADD COLUMN tags text[] NOT NULL DEFAULT '{}',
    ADD CONSTRAINT documentation_prompts_tags CHECK (cardinality(tags) <= 10 AND documentation_prompt_tags_valid(tags)),
    ALTER COLUMN input_kind SET DEFAULT 'mixed';
CREATE INDEX documentation_prompts_tags_gin ON documentation_prompts USING gin (tags);
COMMENT ON COLUMN documentation_prompts.tags IS 'Free-text labels (lowercase, digits, -) that features and filters use to find prompts.';
COMMENT ON COLUMN documentation_prompts.input_kind IS 'Retired; kept for the seed functions. New prompts are classified by tags.';

UPDATE documentation_prompts SET tags = ARRAY['documentation', input_kind];

CREATE FUNCTION documentation_prompt_default_tags() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF cardinality(NEW.tags) = 0 THEN
        NEW.tags := ARRAY['documentation', NEW.input_kind];
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER documentation_prompt_default_tags BEFORE INSERT ON documentation_prompts
    FOR EACH ROW EXECUTE FUNCTION documentation_prompt_default_tags();

COMMIT;
