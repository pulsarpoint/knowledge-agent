BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;  -- the deferrable deployment identity must not leave pending checks before ALTER TABLE

-- Every deployment is in an environment. Deployments still carrying only a free-text label get the environment of
-- that name (case-insensitive), created when missing. The free-text column itself goes in 0126.
INSERT INTO environments (project_id, name)
SELECT DISTINCT project_id, btrim(left(btrim(environment), 100)) FROM service_deployments WHERE environment_id IS NULL
ON CONFLICT (project_id, lower(name)) DO NOTHING;
UPDATE service_deployments d SET environment_id = e.id FROM environments e
WHERE d.environment_id IS NULL AND e.project_id = d.project_id
    AND lower(e.name) = lower(btrim(left(btrim(d.environment), 100)));
ALTER TABLE service_deployments ALTER COLUMN environment_id SET NOT NULL;
-- An environment with deployments cannot be deleted (it used to unlink them).
ALTER TABLE service_deployments DROP CONSTRAINT service_deployments_environment_fk;
ALTER TABLE service_deployments ADD CONSTRAINT service_deployments_environment_fk
    FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id);
ALTER TABLE service_deployments ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0);
COMMENT ON COLUMN service_deployments.environment_id IS 'The environment this deployment runs in; a server or service is in an environment when one of its deployments is.';

-- A network connection from a deployment to another deployment or to an external host:port.
CREATE TABLE connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    from_deployment_id uuid NOT NULL,
    to_deployment_id uuid,
    to_host text CONSTRAINT connections_to_host CHECK (to_host ~ '^[A-Za-z0-9.:-]{1,253}$'),
    port integer CONSTRAINT connections_port CHECK (port BETWEEN 1 AND 65535),
    protocol text NOT NULL DEFAULT 'tcp' CONSTRAINT connections_protocol CHECK (protocol ~ '^[a-z0-9.+-]{1,20}$'),
    description text NOT NULL DEFAULT '' CONSTRAINT connections_description CHECK (length(description) <= 4000),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_by_agent text REFERENCES agents(id) ON DELETE SET NULL,
    session_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, project_id),
    CONSTRAINT connections_one_target CHECK (num_nonnulls(to_deployment_id, to_host) = 1),
    CONSTRAINT connections_not_self CHECK (to_deployment_id IS NULL OR to_deployment_id <> from_deployment_id),
    CONSTRAINT connections_once UNIQUE NULLS NOT DISTINCT (from_deployment_id, to_deployment_id, to_host, port, protocol),
    FOREIGN KEY (from_deployment_id, project_id) REFERENCES service_deployments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_deployment_id, project_id) REFERENCES service_deployments(id, project_id) ON DELETE CASCADE
);
CREATE INDEX connections_to ON connections (to_deployment_id);
CREATE INDEX connections_project ON connections (project_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON connections FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE connections IS 'Network connections: a deployment connects to another deployment or to an external host, on a port with a protocol. May cross environments.';
COMMENT ON COLUMN connections.session_id IS 'ka.agent_sessions.id of the run that created it (no foreign key: the trace outlives the session).';

-- Logical service-level relations that are not network traffic.
CREATE TABLE service_relations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    from_service_id uuid NOT NULL,
    to_service_id uuid,
    to_code_project_id uuid,
    kind text NOT NULL CONSTRAINT service_relations_kind CHECK (kind IN ('depends_on', 'part_of', 'built_from')),
    description text NOT NULL DEFAULT '' CONSTRAINT service_relations_description CHECK (length(description) <= 4000),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_by_agent text REFERENCES agents(id) ON DELETE SET NULL,
    session_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, project_id),
    CONSTRAINT service_relations_target CHECK (
        (kind IN ('depends_on', 'part_of') AND to_service_id IS NOT NULL AND to_code_project_id IS NULL)
        OR (kind = 'built_from' AND to_code_project_id IS NOT NULL AND to_service_id IS NULL)),
    CONSTRAINT service_relations_not_self CHECK (to_service_id IS NULL OR to_service_id <> from_service_id),
    CONSTRAINT service_relations_once UNIQUE NULLS NOT DISTINCT (from_service_id, to_service_id, to_code_project_id, kind),
    FOREIGN KEY (from_service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE
);
CREATE INDEX service_relations_to_service ON service_relations (to_service_id);
CREATE INDEX service_relations_to_code_project ON service_relations (to_code_project_id);
CREATE INDEX service_relations_project ON service_relations (project_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON service_relations FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE service_relations IS 'Service relations: depends_on and part_of (service → service), built_from (service → code project).';
COMMENT ON COLUMN service_relations.session_id IS 'ka.agent_sessions.id of the run that created it (no foreign key: the trace outlives the session).';

-- Convert inventory links (the table itself goes in 0126).
-- depends_on and built_from become relations.
INSERT INTO service_relations (project_id, from_service_id, to_service_id, to_code_project_id, kind, description,
    created_by, created_at, updated_at)
SELECT project_id, from_service_id, to_service_id, to_code_project_id, kind, description, created_by, created_at, updated_at
FROM inventory_links WHERE kind IN ('depends_on', 'built_from');
-- connects_to between two services: one connection per pair of their deployments in the same environment. The port and
-- protocol come from the link's attributes when they are valid, else the target deployment's port and tcp.
INSERT INTO connections (project_id, from_deployment_id, to_deployment_id, port, protocol, description, created_by,
    created_at, updated_at)
SELECT l.project_id, a.id, b.id,
    COALESCE(CASE WHEN l.attributes->>'port' ~ '^[0-9]{1,5}$'
        THEN CASE WHEN (l.attributes->>'port')::integer BETWEEN 1 AND 65535 THEN (l.attributes->>'port')::integer END
        END, b.port),
    CASE WHEN lower(l.attributes->>'protocol') ~ '^[a-z0-9.+-]{1,20}$' THEN lower(l.attributes->>'protocol') ELSE 'tcp' END,
    l.description, l.created_by, l.created_at, l.updated_at
FROM inventory_links l
JOIN service_deployments a ON a.service_id = l.from_service_id
JOIN service_deployments b ON b.service_id = l.to_service_id AND b.environment_id = a.environment_id
WHERE l.kind = 'connects_to'
ON CONFLICT DO NOTHING;
-- ... and a depends_on relation when no such pair exists (either service is not deployed, or not in a shared environment).
INSERT INTO service_relations (project_id, from_service_id, to_service_id, kind, description, created_by, created_at,
    updated_at)
SELECT l.project_id, l.from_service_id, l.to_service_id, 'depends_on', l.description, l.created_by, l.created_at, l.updated_at
FROM inventory_links l
WHERE l.kind = 'connects_to' AND l.from_service_id IS NOT NULL AND l.to_service_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM service_deployments a
        JOIN service_deployments b ON b.service_id = l.to_service_id AND b.environment_id = a.environment_id
        WHERE a.service_id = l.from_service_id)
ON CONFLICT DO NOTHING;
-- connects_to links with an end on a server, an environment or a code project are not converted.
COMMIT;
