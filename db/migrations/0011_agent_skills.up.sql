SET search_path = ka, public;

CREATE TABLE agent_skills (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 64
        AND name ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    description text NOT NULL CHECK (char_length(btrim(description)) BETWEEN 1 AND 1024),
    instructions text NOT NULL CHECK (char_length(btrim(instructions, E' \t\n\r')) BETWEEN 1 AND 100000
        AND char_length(instructions) <= 100000),
    enabled boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid NOT NULL REFERENCES principals(id),
    updated_by uuid NOT NULL REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

CREATE TRIGGER touch_updated_at BEFORE UPDATE ON agent_skills
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMENT ON TABLE agent_skills IS 'Administrator-maintained skill library. Agent assignment and execution are configured separately.';
COMMENT ON COLUMN agent_skills.description IS 'Short explanation of when an agent should use this skill.';
COMMENT ON COLUMN agent_skills.instructions IS 'Markdown instruction body, preserved as entered.';
COMMENT ON COLUMN agent_skills.enabled IS 'Whether this skill is available for agent configuration; disabling preserves its content.';
