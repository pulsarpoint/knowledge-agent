BEGIN;
SET search_path=ka,public;

CREATE TABLE environments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name nonblank NOT NULL CHECK (length(name)<=100 AND name=btrim(name)),
    description text NOT NULL DEFAULT '' CHECK (length(description)<=4000),
    revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id,project_id)
);
CREATE UNIQUE INDEX environments_project_name ON environments(project_id,lower(name));
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON environments
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE environment_services (
    project_id uuid NOT NULL,
    environment_id uuid NOT NULL,
    service_id uuid NOT NULL,
    PRIMARY KEY (environment_id,service_id),
    FOREIGN KEY (environment_id,project_id) REFERENCES environments(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (service_id,project_id) REFERENCES services(id,project_id) ON DELETE CASCADE
);
CREATE INDEX environment_services_service ON environment_services(service_id);

CREATE TABLE environment_servers (
    project_id uuid NOT NULL,
    environment_id uuid NOT NULL,
    server_id uuid NOT NULL,
    PRIMARY KEY (environment_id,server_id),
    FOREIGN KEY (environment_id,project_id) REFERENCES environments(id,project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id,project_id) REFERENCES servers(id,project_id) ON DELETE CASCADE
);
CREATE INDEX environment_servers_server ON environment_servers(server_id);

CREATE VIEW environment_assets AS
SELECT e.id,e.project_id,e.name,e.description,e.revision,e.created_at,e.updated_at,
    COALESCE((SELECT jsonb_agg(jsonb_build_object('id',s.id,'name',s.name)
        ORDER BY lower(s.name),s.id)
        FROM environment_services es JOIN services s ON s.id=es.service_id
        WHERE es.environment_id=e.id),'[]'::jsonb) AS services,
    COALESCE((SELECT jsonb_agg(jsonb_build_object('id',s.id,'name',s.name,'hostname',s.hostname)
        ORDER BY lower(s.name),s.id)
        FROM environment_servers es JOIN servers s ON s.id=es.server_id
        WHERE es.environment_id=e.id),'[]'::jsonb) AS servers
FROM environments e;

COMMENT ON TABLE environments IS 'Named architecture environments. Membership is maintained explicitly, not inferred from deployment labels.';
COMMENT ON TABLE environment_services IS 'Services belonging to an environment; membership alone does not describe dependencies or deployments.';
COMMENT ON TABLE environment_servers IS 'Servers belonging to an environment; membership grants no SSH access.';
COMMENT ON VIEW environment_assets IS 'Environment inventory and member summaries without credentials.';
COMMIT;
