BEGIN;
SET search_path = ka, public;

-- The system graph: four layers (viewpoints), each a tree of nodes — graph in graph, every node is a graph of its
-- children. A node has one parent, in the same project and layer (NULL = top level; the project is the implicit root).
-- Edges join any two nodes of the project, in one layer or across layers, and are stored at the deepest level anyone
-- knows them; the API computes higher levels and never stores them.
CREATE TABLE kg_nodes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    layer text NOT NULL CONSTRAINT kg_nodes_layer CHECK (layer IN ('business', 'software', 'data', 'infrastructure')),
    kind nonblank NOT NULL CONSTRAINT kg_nodes_kind CHECK (length(kind) <= 60),
    name nonblank NOT NULL CONSTRAINT kg_nodes_name CHECK (length(name) <= 160),
    description text NOT NULL DEFAULT '' CONSTRAINT kg_nodes_description CHECK (length(description) <= 4000),
    parent_id uuid,
    properties jsonb NOT NULL DEFAULT '{}' CONSTRAINT kg_nodes_properties
        CHECK (jsonb_typeof(properties) = 'object' AND length(properties::text) <= 16384),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, project_id),
    UNIQUE (id, project_id, layer),
    CONSTRAINT kg_nodes_parent_layer FOREIGN KEY (parent_id, project_id, layer)
        REFERENCES kg_nodes(id, project_id, layer) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX kg_nodes_sibling_name ON kg_nodes (project_id, layer, parent_id, lower(name)) NULLS NOT DISTINCT;
CREATE INDEX kg_nodes_children ON kg_nodes (parent_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON kg_nodes FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON COLUMN kg_nodes.kind IS 'Free text such as service, module, server, table, capability; the UI suggests kinds per layer.';
COMMENT ON COLUMN kg_nodes.properties IS 'Extra facts about the node (port, language, …). Operator context, not instructions.';

-- No node inside itself, and no branch deeper than 12 levels (top level = 1). Placing nodes is serialised per project
-- so two concurrent moves cannot build a cycle together. Errors carry no constraint name; the API shows the sentence.
CREATE FUNCTION kg_nodes_place() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    above integer := 0;
    inside boolean := false;
    height integer := 1;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(NEW.project_id::text));
    IF NEW.parent_id IS NOT NULL THEN
        WITH RECURSIVE up(id, parent_id) AS (
            SELECT id, parent_id FROM kg_nodes WHERE id = NEW.parent_id
            UNION ALL
            SELECT n.id, n.parent_id FROM kg_nodes n JOIN up ON n.id = up.parent_id)
        SELECT count(*), coalesce(bool_or(id = NEW.id), false) INTO above, inside FROM up;
        IF inside OR NEW.parent_id = NEW.id THEN
            RAISE EXCEPTION 'A node cannot be moved inside itself.' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        WITH RECURSIVE down(id, level) AS (
            SELECT NEW.id, 1
            UNION ALL
            SELECT n.id, down.level + 1 FROM kg_nodes n JOIN down ON n.parent_id = down.id)
        SELECT max(level) INTO height FROM down;
    END IF;
    IF above + height > 12 THEN
        RAISE EXCEPTION 'Too deep: a layer has at most 12 levels.' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER kg_nodes_place BEFORE INSERT OR UPDATE OF parent_id ON kg_nodes
    FOR EACH ROW EXECUTE FUNCTION kg_nodes_place();

CREATE TABLE kg_edges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    from_id uuid NOT NULL,
    to_id uuid NOT NULL,
    type text NOT NULL CONSTRAINT kg_edges_type CHECK (type IN ('calls', 'uses', 'depends_on', 'reads', 'writes',
        'publishes', 'subscribes', 'instance_of', 'runs_on', 'implements', 'stores')),
    description text NOT NULL DEFAULT '' CONSTRAINT kg_edges_description CHECK (length(description) <= 4000),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kg_edges_two_nodes CHECK (from_id <> to_id),
    CONSTRAINT kg_edges_once UNIQUE (from_id, to_id, type),
    FOREIGN KEY (from_id, project_id) REFERENCES kg_nodes(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_id, project_id) REFERENCES kg_nodes(id, project_id) ON DELETE CASCADE
);
CREATE INDEX kg_edges_to ON kg_edges (to_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON kg_edges FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE kg_edges IS 'Directed, typed edges between graph nodes. Adding a type is a migration (each type has a diagram style).';

-- Containment already says "inside": an edge between a node and its ancestor or descendant is refused when it is made.
-- A later move may still produce one; views count it as internal. A self-edge is left to kg_edges_two_nodes.
CREATE FUNCTION kg_edges_check() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF NEW.from_id = NEW.to_id THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        WITH RECURSIVE up(id, parent_id) AS (
            SELECT id, parent_id FROM kg_nodes WHERE id = NEW.from_id
            UNION ALL
            SELECT n.id, n.parent_id FROM kg_nodes n JOIN up ON n.id = up.parent_id)
        SELECT 1 FROM up WHERE id = NEW.to_id)
    OR EXISTS (
        WITH RECURSIVE up(id, parent_id) AS (
            SELECT id, parent_id FROM kg_nodes WHERE id = NEW.to_id
            UNION ALL
            SELECT n.id, n.parent_id FROM kg_nodes n JOIN up ON n.id = up.parent_id)
        SELECT 1 FROM up WHERE id = NEW.from_id) THEN
        RAISE EXCEPTION 'An edge cannot join a node and a node that contains it.' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER kg_edges_check BEFORE INSERT ON kg_edges FOR EACH ROW EXECUTE FUNCTION kg_edges_check();

-- Documents attached to a node (the documents themselves live in ka.documents; detaching never deletes them).
CREATE TABLE kg_node_documents (
    node_id uuid NOT NULL,
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id, document_id),
    FOREIGN KEY (node_id, project_id) REFERENCES kg_nodes(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE
);
CREATE INDEX kg_node_documents_document ON kg_node_documents (document_id);

COMMIT;
