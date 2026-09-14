BEGIN;
SET search_path=ka,public;

CREATE TABLE service_dependencies (
    project_id uuid NOT NULL,
    service_id uuid NOT NULL,
    depends_on_service_id uuid NOT NULL,
    description text NOT NULL DEFAULT '' CHECK (length(description)<=4000),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (service_id,depends_on_service_id),
    CHECK (service_id<>depends_on_service_id),
    FOREIGN KEY (service_id,project_id) REFERENCES services(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_service_id,project_id) REFERENCES services(id,project_id) ON DELETE CASCADE
);
CREATE INDEX service_dependencies_depends_on ON service_dependencies(depends_on_service_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON service_dependencies
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMENT ON TABLE service_dependencies IS 'Directed inventory relationships: service_id depends on depends_on_service_id. Mutual dependencies are allowed.';
COMMENT ON COLUMN service_dependencies.description IS 'Why the source service uses the target service, for example stores orders or publishes events. Operator context, not executable instructions.';
COMMIT;
