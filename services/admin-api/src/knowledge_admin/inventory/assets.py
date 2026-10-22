"""The service and environment records the Services and Environments lists show, built from the store's reads (they
replace the service_assets and environment_assets views): a service's environments are those of its deployments, its
code projects its built_from relations, its dependencies and dependents its depends_on relations; an environment holds
the services and servers deployed in it."""

from uuid import UUID

from knowledge_admin.inventory import store

REPOSITORIES = """SELECT id,name,remote_url,requested_ref,current_commit,checkout_path,synced_at,
    archived_at IS NOT NULL AS archived FROM ka.repositories WHERE project_id=%s"""


def _by_name(items) -> list[dict]:
    return sorted(items, key=lambda item: (item['name'].lower(), str(item['id'])))


def service_asset(service: dict, rows: dict) -> dict:
    """One service's record out of the project's rows: {servers, environments, services, repositories} by id and the
    lists deployments and relations."""
    deployments = sorted(
        (
            {
                'id': d['id'],
                'server_id': d['server_id'],
                'environment_id': d['environment_id'],
                'environment': rows['environments'][d['environment_id']]['name'],
                'runtime': d['runtime'],
                'runtime_name': d['runtime_name'],
                'port': d['port'],
                'log_location': d['log_location'],
                'deployed_version': d['deployed_version'],
                'server': {k: v for k, v in rows['servers'][d['server_id']].items() if k != 'project_id'},
            }
            for d in rows['deployments']
            if d['service_id'] == service['id']
        ),
        key=lambda d: (d['environment'], d['server']['name'], d['runtime_name'], str(d['id'])),
    )
    relations = rows['relations']

    def linked(near: str, far: str) -> list[dict]:
        return sorted(
            (
                {'service_id': r[far], 'name': rows['services'][r[far]]['name'], 'description': r['description']}
                for r in relations
                if r['kind'] == 'depends_on' and r[near] == service['id']
            ),
            key=lambda item: (item['name'].lower(), str(item['service_id'])),
        )

    return {
        **service,
        'repositories': _by_name(
            rows['repositories'][r['to_code_project_id']]
            for r in relations
            if r['kind'] == 'built_from' and r['from_service_id'] == service['id']
        ),
        'deployments': deployments,
        'dependencies': linked('from_service_id', 'to_service_id'),
        'dependents': linked('to_service_id', 'from_service_id'),
        'environments': _by_name(
            {'id': key, 'name': rows['environments'][key]['name']} for key in {d['environment_id'] for d in deployments}
        ),
    }


def _rows(conn, project_id: UUID) -> dict:
    return {
        'servers': {s['id']: s for s in store.list_servers(conn, project_id)},
        'services': {s['id']: s for s in store.list_services(conn, project_id)},
        'environments': {e['id']: e for e in store.list_environments(conn, project_id)},
        'repositories': {r['id']: r for r in conn.execute(REPOSITORIES, (project_id,))},
        'deployments': store.list_deployments(conn, project_id),
        'relations': store.list_relations(conn, project_id),
    }


def service_assets(conn, project_id: UUID, service_id: UUID | None = None) -> list[dict]:
    """The project's services (or the one service) as the Services list shows them, by name."""
    rows = _rows(conn, project_id)
    return [
        service_asset(service, rows)
        for service in rows['services'].values()
        if service_id is None or service['id'] == service_id
    ]


def environment_assets(conn, project_id: UUID, environment_id: UUID | None = None) -> list[dict]:
    """The project's environments (or the one environment) with the services and servers deployed in them, by
    name."""
    deployments = store.list_deployments(conn, project_id, environment_id=environment_id)
    services = {s['id']: s for s in store.list_services(conn, project_id)}
    servers = {s['id']: s for s in store.list_servers(conn, project_id)}
    return [
        {
            **environment,
            'services': _by_name(
                {'id': key, 'name': services[key]['name']}
                for key in {d['service_id'] for d in deployments if d['environment_id'] == environment['id']}
            ),
            'servers': _by_name(
                {'id': key, 'name': servers[key]['name'], 'hostname': servers[key]['hostname']}
                for key in {d['server_id'] for d in deployments if d['environment_id'] == environment['id']}
            ),
        }
        for environment in store.list_environments(conn, project_id)
        if environment_id is None or environment['id'] == environment_id
    ]
