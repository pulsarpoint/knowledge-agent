"""The environment model: one environment's servers with their deployments, its services, the connections touching it
(an end in another environment is named in `outside`), external hosts with their ports, and the relations of its
services. build_model is pure: it takes the project's rows (store.load_model_rows) and picks named fields only, so
nothing secret (SSH username, port, access or credential settings) reaches it."""

SERVER_FIELDS = ('id', 'name', 'hostname', 'description', 'tags', 'revision')
SERVICE_FIELDS = ('id', 'name', 'service_type', 'service_role', 'description', 'owner', 'revision')
CONNECTION_FIELDS = (
    'id',
    'from_deployment_id',
    'to_deployment_id',
    'to_host',
    'port',
    'protocol',
    'description',
    'revision',
)


def _pick(row: dict, fields: tuple) -> dict:
    return {field: row[field] for field in fields}


def _by_name(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: (item['name'].lower(), str(item['id'])))


def _relation(r: dict, service_by_id: dict, code_project_by_id: dict) -> dict:
    def name_of(rows: dict, item_id) -> str | None:
        return None if item_id is None else rows[item_id]['name']

    return {
        'id': r['id'],
        'kind': r['kind'],
        'from_service_id': r['from_service_id'],
        'from_service': name_of(service_by_id, r['from_service_id']),
        'to_service_id': r['to_service_id'],
        'to_service': name_of(service_by_id, r['to_service_id']),
        'to_code_project_id': r['to_code_project_id'],
        'to_code_project': name_of(code_project_by_id, r['to_code_project_id']),
        'description': r['description'],
        'revision': r['revision'],
    }


def build_model(
    environment: dict,
    servers: list[dict],
    services: list[dict],
    deployments: list[dict],
    connections: list[dict],
    relations: list[dict],
    code_projects: list[dict],
) -> dict:
    """Rows are the project's (deployments carry environment_name); connections and relations keep their order."""
    server_by_id = {s['id']: s for s in servers}
    service_by_id = {s['id']: s for s in services}
    code_project_by_id = {c['id']: c for c in code_projects}
    deployment_by_id = {d['id']: d for d in deployments}
    inside = [d for d in deployments if d['environment_id'] == environment['id']]
    inside_ids = {d['id'] for d in inside}
    service_ids = {d['service_id'] for d in inside}

    def deployment(d: dict) -> dict:
        return {
            'id': d['id'],
            'service_id': d['service_id'],
            'service_name': service_by_id[d['service_id']]['name'],
            'runtime': d['runtime'],
            'runtime_name': d['runtime_name'],
            'port': d['port'],
            'version': d['deployed_version'],
            'log_location': d['log_location'],
            'revision': d['revision'],
        }

    def on_server(server_id) -> list[dict]:
        found = [deployment(d) for d in inside if d['server_id'] == server_id]
        return sorted(found, key=lambda d: (d['service_name'].lower(), d['runtime_name'] or '', str(d['id'])))

    model_servers = [
        {**_pick(s, SERVER_FIELDS), 'deployments': on_server(s['id'])}
        for s in _by_name([server_by_id[i] for i in {d['server_id'] for d in inside}])
    ]

    model_connections = []
    external: dict[str, set] = {}
    for c in connections:
        ends = [c['from_deployment_id'], c['to_deployment_id']]
        if not inside_ids.intersection(ends):
            continue
        item = _pick(c, CONNECTION_FIELDS)
        outside = [end for end in ends if end is not None and end not in inside_ids]
        if outside:
            other = deployment_by_id[outside[0]]
            item['outside'] = {
                'deployment_id': other['id'],
                'environment': other['environment_name'],
                'server': server_by_id[other['server_id']]['name'],
                'service': service_by_id[other['service_id']]['name'],
            }
        if c['to_host'] is not None:
            ports = external.setdefault(c['to_host'], set())
            if c['port'] is not None:
                ports.add(c['port'])
        model_connections.append(item)

    model_relations = [
        _relation(r, service_by_id, code_project_by_id)
        for r in relations
        if r['from_service_id'] in service_ids or r['to_service_id'] in service_ids
    ]

    return {
        'environment': _pick(environment, ('id', 'name', 'description', 'revision')),
        'servers': model_servers,
        'services': [_pick(s, SERVICE_FIELDS) for s in _by_name([service_by_id[i] for i in service_ids])],
        'connections': model_connections,
        'relations': model_relations,
        'external': [{'host': host, 'ports': sorted(ports)} for host, ports in sorted(external.items())],
    }


def build_project(
    environments: list[dict],
    servers: list[dict],
    services: list[dict],
    deployments: list[dict],
    connections: list[dict],
    relations: list[dict],
    code_projects: list[dict],
) -> dict:
    """The model of every environment (by name) and what no environment holds: the servers and services with no
    deployment, and the relations no model lists (between such services, or from one to a code project)."""
    models = [
        build_model(e, servers, services, deployments, connections, relations, code_projects)
        for e in _by_name(environments)
    ]
    placed_servers = {d['server_id'] for d in deployments}
    placed_services = {d['service_id'] for d in deployments}
    held = {r['id'] for m in models for r in m['relations']}
    service_by_id = {s['id']: s for s in services}
    code_project_by_id = {c['id']: c for c in code_projects}
    return {
        'models': models,
        'unplaced': {
            'servers': [
                _pick(s, SERVER_FIELDS) for s in _by_name([s for s in servers if s['id'] not in placed_servers])
            ],
            'services': [
                _pick(s, SERVICE_FIELDS) for s in _by_name([s for s in services if s['id'] not in placed_services])
            ],
            'relations': [_relation(r, service_by_id, code_project_by_id) for r in relations if r['id'] not in held],
        },
    }
