"""save_inventory_draft: the pure resolver (resolve_draft over the payload and the rows the run may name) and the tool
end to end (one transaction, everything a draft, all or nothing)."""

import pytest
from knowledge_admin.capabilities import inventory_tools as tools
from knowledge_admin.capabilities.inventory_tools import Ref, resolve_draft
from knowledge_admin.inventory import store as inventory_store
from knowledge_admin.inventory.rules import Invalid
from test_inventory_tools import call, cfg, i, world  # noqa: F401  (world is a fixture)

# The spec's example (docs/superpowers/specs/2026-10-23-inventory-drafts-design.md, section 2).
EXAMPLE = {
    'environment': 'prod',
    'servers': [{'name': 'app-1', 'hostname': '10.0.0.5', 'description': 'Docker host'}],
    'services': [
        {'name': 'clickhouse', 'service_type': 'builtin', 'service_role': 'database', 'description': 'Analytics store'},
        {'name': 'clickhouse-backup', 'service_type': 'builtin', 'service_role': 'worker', 'part_of': ['clickhouse']},
        {'name': 'backblaze-b2', 'service_type': 'external', 'address': 's3.us-east-005.backblazeb2.com'},
    ],
    'deployments': [
        {
            'service': 'clickhouse',
            'server': 'app-1',
            'runtime': 'docker compose',
            'runtime_name': 'clickhouse',
            'port': 9000,
            'version': 'clickhouse/clickhouse-server:24.8',
        },
        {
            'service': 'clickhouse-backup',
            'server': 'app-1',
            'runtime': 'docker compose',
            'runtime_name': 'clickhouse-backup',
            'version': 'altinity/clickhouse-backup:2.7.4',
        },
    ],
    'connections': [
        {
            'from': 'clickhouse-backup',
            'from_server': 'app-1',
            'to': 'clickhouse',
            'to_server': 'app-1',
            'port': 9000,
            'protocol': 'tcp',
        },
        {'from': 'clickhouse-backup', 'to': 'backblaze-b2', 'port': 443, 'protocol': 'https'},
        {'from': 'clickhouse', 'to_host': 'smtp.example.com', 'port': 25, 'protocol': 'smtp'},
    ],
    'relations': [{'from': 'clickhouse-backup', 'kind': 'depends_on', 'to': 'clickhouse'}],
}


def payload(**parts) -> dict:
    """A payload as the tool's argument model dumps it: every part present (None when not given), each entry with
    every field (None when not given); 'from' is the field from_."""
    fields = {
        'servers': ('name', 'hostname', 'description', 'tags'),
        'services': ('name', 'service_type', 'service_role', 'description', 'owner', 'address', 'part_of'),
        'deployments': ('service', 'server', 'runtime', 'runtime_name', 'port', 'version', 'log_location'),
        'connections': ('from_', 'from_server', 'to', 'to_server', 'to_host', 'port', 'protocol', 'description'),
        'relations': ('from_', 'kind', 'to', 'description'),
    }
    found = {'environment': parts.get('environment')}
    for part, names in fields.items():
        given = parts.get(part)
        found[part] = None if given is None else [{n: e.get(n.rstrip('_')) for n in names} for e in given]
    return found


# What a run on environment prod may name: server app-1 (confirmed) and app-2 (a draft), both in prod, clickhouse
# (confirmed) deployed on both, old-backup (a draft), the external service s3 (confirmed), code project payments-api.
RECORDED = {
    'may_create': True,
    'environments': [{'id': 'e1', 'name': 'prod', 'status': 'confirmed', 'revision': 1}],
    'servers': [
        {'id': 's1', 'name': 'app-1', 'status': 'confirmed', 'revision': 1, 'environment_ids': ['e1']},
        {'id': 's2', 'name': 'app-2', 'status': 'draft', 'revision': 2, 'environment_ids': ['e1']},
    ],
    'services': [
        {'id': 'v1', 'name': 'clickhouse', 'status': 'confirmed', 'revision': 1, 'service_type': 'builtin'},
        {'id': 'v2', 'name': 'old-backup', 'status': 'draft', 'revision': 3, 'service_type': 'builtin'},
        {'id': 'v3', 'name': 's3', 'status': 'confirmed', 'revision': 1, 'service_type': 'external'},
    ],
    'deployments': [
        {
            'id': 'p1',
            'service_id': 'v1',
            'server_id': 's1',
            'environment_id': 'e1',
            'runtime_name': 'clickhouse',
            'port': 9000,
            'status': 'confirmed',
            'revision': 1,
        },
        {
            'id': 'p2',
            'service_id': 'v1',
            'server_id': 's2',
            'environment_id': 'e1',
            'runtime_name': 'clickhouse',
            'port': 9000,
            'status': 'draft',
            'revision': 1,
        },
    ],
    'connections': [
        {
            'id': 'n1',
            'from_deployment_id': 'p1',
            'to_deployment_id': None,
            'to_host': None,
            'to_service_id': 'v3',
            'port': 443,
            'protocol': 'https',
            'status': 'draft',
            'revision': 4,
        }
    ],
    'relations': [],
    'code_projects': [{'id': 'c1', 'name': 'payments-api'}],
}


def error_of(value: dict, recorded: dict = RECORDED) -> str:
    with pytest.raises(Invalid) as raised:
        resolve_draft(value, recorded)
    return str(raised.value)


def steps(plan: list[dict], kind: str) -> list[dict]:
    return [step for step in plan if step['kind'] == kind]


# --- the resolver (pure) ---


def test_names_in_the_call_come_first_then_the_recorded_ones():
    plan = resolve_draft(
        payload(
            environment='prod',
            services=[{'name': 'redis'}],
            deployments=[{'service': 'redis', 'server': 'app-1', 'port': 6379}],
            connections=[{'from': 'redis', 'to': 'clickhouse', 'to_server': 'app-1'}],
        ),
        RECORDED,
    )
    [environment] = steps(plan, 'environment')
    assert (environment['action'], environment['id']) == ('use', 'e1')
    [service] = steps(plan, 'service')
    assert (service['action'], service['values']) == ('create', {'name': 'redis'})
    [deployment] = steps(plan, 'deployment')
    assert deployment['action'] == 'create'
    assert deployment['values'] == {
        'service_id': Ref(service['key']),
        'server_id': 's1',
        'environment_id': 'e1',
        'port': 6379,
    }
    [connection] = steps(plan, 'connection')
    assert connection['values'] == {'from_deployment_id': Ref(deployment['key']), 'to_deployment_id': 'p1'}
    assert connection['name'] == 'redis → clickhouse'


def test_a_service_on_several_servers_needs_the_server():
    value = payload(environment='prod', connections=[{'from': 'clickhouse', 'to_host': 'smtp.example.com', 'port': 25}])
    assert error_of(value) == 'connections[0]: clickhouse runs on app-1, app-2; give from_server.'
    value = payload(environment='prod', services=[{'name': 'web'}], deployments=[{'service': 'web', 'server': 'app-1'}])
    value['connections'] = payload(connections=[{'from': 'web', 'to': 'clickhouse'}])['connections']
    assert error_of(value) == 'connections[0]: clickhouse runs on app-1, app-2; give to_server.'
    plan = resolve_draft(
        payload(
            environment='prod',
            connections=[{'from': 'clickhouse', 'from_server': 'app-2', 'to_host': 'smtp.example.com', 'port': 25}],
        ),
        RECORDED,
    )
    assert steps(plan, 'connection')[0]['values']['from_deployment_id'] == 'p2'


def test_an_unknown_name_is_refused_with_the_known_ones():
    value = payload(
        environment='prod',
        services=[{'name': 'clickhouse-backup'}],
        connections=[{'from': 'clickhous', 'to_host': 'smtp.example.com', 'port': 25}],
    )
    assert error_of(value) == (
        'connections[0]: no service named clickhous. Known services: clickhouse-backup, clickhouse, old-backup, s3.'
    )
    value = payload(environment='prod', deployments=[{'service': 'clickhouse', 'server': 'db-9'}])
    assert error_of(value) == 'deployments[0]: no server named db-9. Known servers: app-1, app-2.'
    value = payload(relations=[{'from': 'clickhouse', 'kind': 'built_from', 'to': 'billing'}])
    assert error_of(value) == 'relations[0]: no code project named billing. Known code projects: payments-api.'
    value = payload(environment='prod', connections=[{'from': 's3', 'to_host': 'x.example', 'port': 1}])
    assert error_of(value) == 'connections[0]: s3 has no deployment in prod.'


def test_a_confirmed_entry_is_used_never_changed_and_a_draft_is_updated():
    plan = resolve_draft(
        payload(
            servers=[{'name': 'APP-1', 'hostname': '10.9.9.9'}, {'name': 'app-2', 'description': 'spare'}],
            services=[{'name': 'clickhouse', 'description': 'something else'}, {'name': 'old-backup', 'owner': 'ops'}],
        ),
        RECORDED,
    )
    app_1, app_2 = steps(plan, 'server')
    assert (app_1['action'], app_1['id'], app_1['values'], app_1['name']) == ('use', 's1', {}, 'app-1')
    assert (app_2['action'], app_2['id'], app_2['revision'], app_2['values']) == (
        'update',
        's2',
        2,
        {'description': 'spare'},
    )
    clickhouse, old = steps(plan, 'service')
    assert (clickhouse['action'], clickhouse['values']) == ('use', {})
    assert (old['action'], old['revision'], old['values']) == ('update', 3, {'owner': 'ops'})


def test_a_recorded_deployment_connection_and_relation_are_matched_not_duplicated():
    plan = resolve_draft(
        payload(
            environment='prod',
            deployments=[
                {'service': 'clickhouse', 'server': 'app-1', 'runtime_name': 'clickhouse', 'port': 9001},
                {'service': 'clickhouse', 'server': 'app-2', 'runtime_name': 'clickhouse', 'version': '24.8'},
            ],
            connections=[
                {
                    'from': 'clickhouse',
                    'from_server': 'app-1',
                    'to': 's3',
                    'port': 443,
                    'protocol': 'https',
                    'description': 'backups',
                }
            ],
        ),
        RECORDED,
    )
    confirmed, draft = steps(plan, 'deployment')
    assert (confirmed['action'], confirmed['id']) == ('use', 'p1')
    assert (draft['action'], draft['id'], draft['values']) == ('update', 'p2', {'deployed_version': '24.8'})
    [connection] = steps(plan, 'connection')
    assert (connection['action'], connection['id'], connection['revision'], connection['values']) == (
        'update',
        'n1',
        4,
        {'description': 'backups'},
    )


def test_targets_external_services_hosts_and_code_projects():
    plan = resolve_draft(
        payload(
            environment='prod',
            connections=[
                {'from': 'clickhouse', 'from_server': 'app-1', 'to': 's3', 'port': 9443},
                {'from': 'clickhouse', 'from_server': 'app-1', 'to_host': 'SMTP.example.com', 'port': 25},
            ],
            relations=[
                {'from': 'clickhouse', 'kind': 'built_from', 'to': 'payments-api'},
                {'from': 'old-backup', 'kind': 'depends_on', 'to': 'clickhouse', 'description': 'reads it'},
            ],
        ),
        RECORDED,
    )
    external, host = steps(plan, 'connection')
    assert external['values'] == {'from_deployment_id': 'p1', 'to_service_id': 'v3', 'port': 9443}
    assert host['values'] == {'from_deployment_id': 'p1', 'to_host': 'SMTP.example.com', 'port': 25}
    assert host['name'] == 'clickhouse → SMTP.example.com'
    built, depends = steps(plan, 'relation')
    assert built['values'] == {'from_service_id': 'v1', 'kind': 'built_from', 'to_code_project_id': 'c1'}
    assert depends['values'] == {
        'from_service_id': 'v2',
        'kind': 'depends_on',
        'to_service_id': 'v1',
        'description': 'reads it',
    }
    assert depends['name'] == 'old-backup depends_on clickhouse'
    both = payload(environment='prod', connections=[{'from': 'old-backup', 'to': 's3', 'to_host': 'x.example'}])
    assert error_of(both) == 'connections[0]: give to (a service) or to_host, not both.'
    neither = payload(environment='prod', connections=[{'from': 'old-backup'}])
    assert error_of(neither) == 'connections[0]: give to (a service) or to_host.'


def test_new_entries_need_an_environment_in_the_run():
    closed = {**RECORDED, 'may_create': False}
    assert error_of(payload(servers=[{'name': 'new', 'hostname': 'new.internal'}]), closed) == (
        'servers[0]: This run has no environments.'
    )
    assert error_of(payload(environment='staging'), closed) == 'environment: This run has no environments.'
    # naming what is recorded needs nothing more
    plan = resolve_draft(payload(servers=[{'name': 'app-2', 'description': 'x'}]), closed)
    assert steps(plan, 'server')[0]['action'] == 'update'


def test_deployments_need_the_environment_and_names_are_listed_once():
    assert error_of(payload(deployments=[{'service': 'clickhouse', 'server': 'app-1'}])) == (
        'deployments[0]: give the environment the deployments are in.'
    )
    twice = payload(servers=[{'name': 'web', 'hostname': 'a.internal'}, {'name': 'Web', 'hostname': 'b.internal'}])
    assert error_of(twice) == 'servers[1]: Web is listed twice.'


def test_servers_join_the_environment_unless_they_are_members():
    plan = resolve_draft(
        payload(environment='prod', servers=[{'name': 'app-1'}, {'name': 'cache', 'hostname': 'cache.internal'}]),
        RECORDED,
    )
    [membership] = steps(plan, 'membership')  # app-1 is in prod already
    assert membership['values'] == {'environment_id': 'e1', 'server_id': Ref(('server', 'cache'))}
    assert membership['name'] == 'cache in prod'


def test_the_spec_example_drafts_everything_it_describes():
    empty = {'may_create': True}
    plan = resolve_draft(payload(**EXAMPLE), empty)
    counts = {kind: len(steps(plan, kind)) for kind in {step['kind'] for step in plan}}
    assert counts == {
        'environment': 1,
        'server': 1,
        'membership': 1,
        'service': 3,
        'relation': 2,  # part_of and depends_on
        'deployment': 2,
        'connection': 3,
    }
    assert {step['action'] for step in plan} == {'create', 'add'}  # add: the membership
    part_of = [s for s in steps(plan, 'relation') if s['values']['kind'] == 'part_of']
    assert part_of[0]['name'] == 'clickhouse-backup part_of clickhouse'
    to_b2 = steps(plan, 'connection')[1]
    assert to_b2['values']['to_service_id'] == Ref(('service', 'backblaze-b2'))
    deployment = steps(plan, 'deployment')[0]
    assert deployment['values']['deployed_version'] == 'clickhouse/clickhouse-server:24.8'
    assert deployment['name'] == 'clickhouse on app-1 in prod'


# --- the tool, end to end ---


def batch(config: dict, value: dict) -> dict:
    return call(config, 'save_inventory_draft', **value)


def statuses(store, table: str) -> dict:
    with store.connect() as conn:
        return {r['id']: r['status'] for r in conn.execute(f'SELECT id,status FROM ka.{table}').fetchall()}


def counts(store) -> dict:
    tables = ('environments', 'servers', 'services', 'service_deployments', 'connections', 'service_relations')
    with store.connect() as conn:
        found = {t: conn.execute(f'SELECT count(*) AS n FROM ka.{t}').fetchone()['n'] for t in tables}
        found['environment_servers'] = conn.execute('SELECT count(*) AS n FROM ka.environment_servers').fetchone()['n']
    return found


def test_the_example_is_drafted_in_one_call(world):  # noqa: F811
    out = batch(world['config'], EXAMPLE)
    assert 'refused' not in out, out
    assert [(e['kind'], e['name'], e['id']) for e in out['used']] == [('environment', 'prod', i(world['prod']))]
    created = {(e['kind'], e['name']) for e in out['created']}
    assert {
        ('server', 'app-1'),
        ('membership', 'app-1 in prod'),
        ('service', 'clickhouse'),
        ('service', 'clickhouse-backup'),
        ('service', 'backblaze-b2'),
        ('relation', 'clickhouse-backup part_of clickhouse'),
        ('deployment', 'clickhouse on app-1 in prod'),
        ('deployment', 'clickhouse-backup on app-1 in prod'),
        ('connection', 'clickhouse-backup → clickhouse'),
        ('connection', 'clickhouse-backup → backblaze-b2'),
        ('connection', 'clickhouse → smtp.example.com'),
        ('relation', 'clickhouse-backup depends_on clickhouse'),
    } == created
    assert out['updated'] == []
    assert 'drafts' in out['message'] and 'confirm' in out['message']
    ids = {e['id'] for e in out['created'] if e['kind'] != 'membership'}
    for table in ('servers', 'services', 'service_deployments', 'connections', 'service_relations'):
        for row_id, status in statuses(world['store'], table).items():
            if str(row_id) in ids:
                assert status == 'draft', (table, row_id)
    assert statuses(world['store'], 'environments')[world['prod']['id']] == 'confirmed'
    model = call(world['config'], 'get_environment_model', environment_id=i(world['prod']))
    assert {s['name']: s['status'] for s in model['servers']}['app-1'] == 'draft'
    # the same picture again: the drafts are updated or used, nothing is duplicated
    before = counts(world['store'])
    again = batch(world['config'], EXAMPLE)
    assert again['created'] == [] and counts(world['store']) == before, again


def test_a_confirmed_service_named_with_other_fields_is_used_unchanged(world):  # noqa: F811
    """Review Focus 3."""
    out = batch(
        world['config'],
        {'services': [{'name': 'api', 'description': 'Rewritten by the agent', 'owner': 'someone'}]},
    )
    assert out['used'] == [{'kind': 'service', 'name': 'api', 'id': i(world['api'])}]
    assert out['created'] == [] and out['updated'] == []
    with world['store'].connect() as conn:
        api = inventory_store.get(conn, world['project'], 'service', world['api']['id'])
    assert (api['description'], api['owner'], api['revision'], api['status']) == ('', '', 1, 'confirmed')


def test_one_bad_name_writes_nothing(world):  # noqa: F811
    """Review Focus 4."""
    before = counts(world['store'])
    bad = {
        **EXAMPLE,
        'connections': [*EXAMPLE['connections'], {'from': 'clickhous', 'to_host': 'x.example', 'port': 1}],
    }
    out = batch(world['config'], bad)
    assert out == {
        'refused': 'connections[3]: no service named clickhous. Known services: clickhouse, clickhouse-backup, '
        'backblaze-b2, api, db.'
    }
    assert counts(world['store']) == before
    # a store error late in the call rolls back what came before it, and names the entry
    late = {**EXAMPLE, 'connections': [{'from': 'clickhouse', 'to_host': 'http://x', 'port': 1}]}
    out = batch(world['config'], late)
    assert out == {'refused': 'connections[0]: Enter an IP address or hostname without a URL, port, or credentials'}
    assert counts(world['store']) == before


def test_the_batch_respects_the_runs_access(world):  # noqa: F811
    c = world['config']
    assert batch(c, {'environment': 'staging'}) == {
        'refused': 'environment: An environment with this name already exists'
    }  # staging is outside the access: a new draft would duplicate it
    out = batch(c, {'environment': 'prod', 'deployments': [{'service': 'web', 'server': 'a'}]})
    assert out == {'refused': 'deployments[0]: no service named web. Known services: api, db.'}
    assert batch(cfg(world, session_id=None), {'environment': 'prod'}) == {'refused': tools.NO_SESSION}


def test_a_new_environment_is_drafted_and_named_again_by_the_session(world):  # noqa: F811
    out = batch(world['config'], {'environment': 'dr'})
    [created] = out['created']
    assert (created['kind'], created['name']) == ('environment', 'dr')
    again = batch(world['config'], {'environment': 'dr', 'servers': [{'name': 'a'}]})
    assert again['used'][0] == created  # created by this session: recorded, a draft without fields to change
    assert [(e['kind'], e['name']) for e in again['created']] == [('membership', 'a in dr')]
