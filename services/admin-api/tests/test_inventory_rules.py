"""inventory/rules.py: pure validation of each inventory item; returns the full, normalized item."""

from uuid import uuid4

import pytest
from knowledge_admin.inventory import rules
from knowledge_admin.inventory.rules import Invalid

A, B, C = uuid4(), uuid4(), uuid4()


def test_server_normalizes_and_defaults():
    assert rules.server({'name': ' web-1 ', 'hostname': 'Web-1.Example.com.', 'tags': [' Prod', 'prod', 'eu']}) == {
        'name': 'web-1',
        'hostname': 'web-1.example.com',
        'description': '',
        'tags': ['prod', 'eu'],
        'ssh_allowed': False,
    }
    assert rules.server({'name': 'db', 'hostname': '10.0.0.5', 'ssh_allowed': True})['hostname'] == '10.0.0.5'


@pytest.mark.parametrize(
    'values',
    [
        {'hostname': 'a'},
        {'name': ' ', 'hostname': 'a'},
        {'name': 'x' * 201, 'hostname': 'a'},
        {'name': 'a', 'hostname': 'ssh://a'},
        {'name': 'a', 'hostname': 'user@a'},
        {'name': 'a', 'hostname': 'a', 'tags': ['a,b']},
        {'name': 'a', 'hostname': 'a', 'tags': [str(i) for i in range(33)]},
        {'name': 'a', 'hostname': 'a', 'ssh_allowed': 'yes'},
        {'name': 'a', 'hostname': 'a', 'description': 'x' * 4001},
        {'name': 'a\x00', 'hostname': 'a'},
        {'name': 'a', 'hostname': 'a', 'enabled': True},
    ],
)
def test_server_rejects(values):
    with pytest.raises(Invalid):
        rules.server(values)


def test_service_normalizes_and_defaults():
    assert rules.service({'name': ' api ', 'service_role': 'database'}) == {
        'name': 'api',
        'service_type': 'unclassified',
        'service_role': 'database',
        'description': '',
        'owner': '',
        'address': None,
    }
    external = rules.service({'name': 'mail', 'service_type': 'external', 'address': 'Mail.Example.com.'})
    assert external['address'] == 'mail.example.com'
    assert rules.service({'name': 'mail', 'address': ''})['address'] is None


@pytest.mark.parametrize(
    'values',
    [
        {},
        {'name': 'a', 'service_type': 'thing'},
        {'name': 'a', 'service_role': 'db'},
        {'name': 'a', 'owner': 'x' * 201},
        {'name': 'a', 'address': 'https://mail.example.com'},
        {'name': 'a', 'address': 'mail.example.com:25'},
        {'name': 'a', 'address': 'x' * 254},
    ],
)
def test_service_rejects(values):
    with pytest.raises(Invalid):
        rules.service(values)


def test_environment():
    assert rules.environment({'name': ' prod '}) == {'name': 'prod', 'description': ''}
    for values in ({}, {'name': 'x' * 101}, {'name': 'a', 'description': 'x' * 4001}):
        with pytest.raises(Invalid):
            rules.environment(values)


def test_deployment_normalizes_and_defaults():
    assert rules.deployment({'service_id': str(A), 'server_id': B, 'environment_id': C, 'port': 8080}) == {
        'service_id': A,
        'server_id': B,
        'environment_id': C,
        'runtime': '',
        'runtime_name': '',
        'port': 8080,
        'log_location': '',
        'deployed_version': '',
    }


@pytest.mark.parametrize(
    'values',
    [
        {'server_id': B, 'environment_id': C},
        {'service_id': A, 'environment_id': C},
        {'service_id': A, 'server_id': B},
        {'service_id': 'nope', 'server_id': B, 'environment_id': C},
        {'service_id': A, 'server_id': B, 'environment_id': C, 'port': 0},
        {'service_id': A, 'server_id': B, 'environment_id': C, 'port': 65536},
        {'service_id': A, 'server_id': B, 'environment_id': C, 'port': '80'},
        {'service_id': A, 'server_id': B, 'environment_id': C, 'port': True},
        {'service_id': A, 'server_id': B, 'environment_id': C, 'runtime': 'x' * 81},
        {'service_id': A, 'server_id': B, 'environment_id': C, 'environment': 'prod'},
    ],
)
def test_deployment_rejects(values):
    with pytest.raises(Invalid):
        rules.deployment(values)


def test_connection_to_a_deployment_or_a_host():
    assert rules.connection({'from_deployment_id': A, 'to_deployment_id': B}) == {
        'from_deployment_id': A,
        'to_deployment_id': B,
        'to_host': None,
        'to_service_id': None,
        'port': None,
        'protocol': 'tcp',
        'description': '',
    }
    assert rules.connection(
        {'from_deployment_id': A, 'to_host': 'DB.Example.com', 'port': 5432, 'protocol': 'postgres'}
    ) == {
        'from_deployment_id': A,
        'to_deployment_id': None,
        'to_host': 'db.example.com',
        'to_service_id': None,
        'port': 5432,
        'protocol': 'postgres',
        'description': '',
    }
    assert rules.connection({'from_deployment_id': A, 'to_host': '::1', 'port': 1})['to_host'] == '::1'
    with pytest.raises(Invalid, match='without a zone id'):
        rules.connection({'from_deployment_id': A, 'to_host': 'fe80::1%eth0', 'port': 1})


def test_connection_to_an_external_service():
    item = rules.connection({'from_deployment_id': A, 'to_service_id': str(C), 'port': 25, 'protocol': 'smtp'})
    assert (item['to_service_id'], item['to_deployment_id'], item['to_host'], item['port']) == (C, None, None, 25)
    assert rules.connection({'from_deployment_id': A, 'to_service_id': C})['port'] is None


def test_only_an_external_service_is_a_connection_target():
    assert rules.check_target_service('external') is None
    for service_type in ('custom', 'builtin', 'unclassified'):
        assert rules.check_target_service(service_type) == (
            'Connect to an external service, or to a deployment of this service.'
        )


def test_a_targeted_service_keeps_the_type_external():
    text = 'This service is the target of connections; remove them or keep the type external.'
    assert rules.check_type_change('external', 'custom', targeted=True) == text
    assert rules.check_type_change('external', 'custom', targeted=False) is None
    assert rules.check_type_change('external', 'external', targeted=True) is None
    assert rules.check_type_change('custom', 'external', targeted=False) is None


@pytest.mark.parametrize(
    'values',
    [
        {'to_deployment_id': B},
        {'from_deployment_id': A},
        {'from_deployment_id': A, 'to_deployment_id': B, 'to_host': 'db', 'port': 1},
        {'from_deployment_id': A, 'to_deployment_id': B, 'to_service_id': C},
        {'from_deployment_id': A, 'to_host': 'db', 'port': 1, 'to_service_id': C},
        {'from_deployment_id': A, 'to_service_id': 'nope'},
        {'from_deployment_id': A, 'to_deployment_id': A},
        {'from_deployment_id': A, 'to_host': 'db'},
        {'from_deployment_id': A, 'to_host': 'db:5432', 'port': 5432},
        {'from_deployment_id': A, 'to_host': 'x' * 254, 'port': 1},
        {'from_deployment_id': A, 'to_deployment_id': B, 'port': 70000},
        {'from_deployment_id': A, 'to_deployment_id': B, 'protocol': ''},
        {'from_deployment_id': A, 'to_deployment_id': B, 'protocol': 'HTTP'},
        {'from_deployment_id': A, 'to_deployment_id': B, 'protocol': 'x' * 21},
        {'from_deployment_id': A, 'to_deployment_id': B, 'protocol': 'tcp udp'},
        {'from_deployment_id': A, 'to_deployment_id': B, 'description': 'x' * 4001},
    ],
)
def test_connection_rejects(values):
    with pytest.raises(Invalid):
        rules.connection(values)


def test_relation_targets_match_the_kind():
    assert rules.relation({'from_service_id': A, 'kind': 'part_of', 'to_service_id': B}) == {
        'from_service_id': A,
        'kind': 'part_of',
        'to_service_id': B,
        'to_code_project_id': None,
        'description': '',
    }
    assert (
        rules.relation({'from_service_id': A, 'kind': 'built_from', 'to_code_project_id': C})['to_code_project_id'] == C
    )


@pytest.mark.parametrize(
    'values',
    [
        {'from_service_id': A, 'to_service_id': B},
        {'from_service_id': A, 'kind': 'calls', 'to_service_id': B},
        {'from_service_id': A, 'kind': 'depends_on', 'to_code_project_id': C},
        {'from_service_id': A, 'kind': 'built_from', 'to_service_id': B},
        {'from_service_id': A, 'kind': 'depends_on', 'to_service_id': B, 'to_code_project_id': C},
        {'from_service_id': A, 'kind': 'depends_on', 'to_service_id': A},
        {'kind': 'depends_on', 'to_service_id': B},
        {'from_service_id': A, 'kind': 'depends_on', 'to_service_id': B, 'description': 'x' * 4001},
    ],
)
def test_relation_rejects(values):
    with pytest.raises(Invalid):
        rules.relation(values)


# Drafts: what Confirm and Discard reach (rows keyed by kind, then id; a membership's id is <environment>:<server>).


def draft_rows(confirmed: set[str] = frozenset()) -> dict:
    """Everything is a draft except the ids in `confirmed`: the environment test with server db (membership) running
    pg (deployment pg_db) and api (api_db); api_db connects to pg_db and to the external service mail; api depends on
    pg; api is built from the code project code."""

    def status(key: str) -> str:
        return 'confirmed' if key in confirmed else 'draft'

    def named(*keys: str) -> dict:
        return {key: {'id': key, 'name': key, 'status': status(key)} for key in keys}

    def deployment(key, service, server, environment):
        return {
            'id': key,
            'service_id': service,
            'server_id': server,
            'environment_id': environment,
            'runtime_name': '',
            'status': status(key),
        }

    return {
        'environment': named('test'),
        'server': named('db'),
        'service': named('pg', 'api', 'mail'),
        'membership': {'test:db': {'environment_id': 'test', 'server_id': 'db', 'status': status('test:db')}},
        'deployment': {
            'pg_db': deployment('pg_db', 'pg', 'db', 'test'),
            'api_db': deployment('api_db', 'api', 'db', 'test'),
        },
        'connection': {
            'c1': {
                'id': 'c1',
                'from_deployment_id': 'api_db',
                'to_deployment_id': 'pg_db',
                'to_service_id': None,
                'to_host': None,
                'status': status('c1'),
            },
            'c2': {
                'id': 'c2',
                'from_deployment_id': 'api_db',
                'to_deployment_id': None,
                'to_service_id': 'mail',
                'to_host': None,
                'status': status('c2'),
            },
        },
        'relation': {
            'r1': {
                'id': 'r1',
                'from_service_id': 'api',
                'kind': 'depends_on',
                'to_service_id': 'pg',
                'to_code_project_id': None,
                'status': status('r1'),
            },
            'r2': {
                'id': 'r2',
                'from_service_id': 'api',
                'kind': 'built_from',
                'to_service_id': None,
                'to_code_project_id': 'code',
                'status': status('r2'),
            },
        },
        'code_project': {'code': {'id': 'code', 'name': 'code'}},
    }


def test_confirming_a_connection_confirms_its_deployments_and_everything_they_need():
    reach = rules.confirm_reach('connection', 'c1', draft_rows())
    assert reach[0] == ('connection', 'c1')
    assert set(reach) == {
        ('connection', 'c1'),
        ('deployment', 'api_db'),
        ('deployment', 'pg_db'),
        ('service', 'api'),
        ('service', 'pg'),
        ('server', 'db'),
        ('membership', 'test:db'),
        ('environment', 'test'),
    }
    assert len(reach) == len(set(reach))
    external = rules.confirm_reach('connection', 'c2', draft_rows())
    assert ('service', 'mail') in external and ('deployment', 'pg_db') not in external


@pytest.mark.parametrize(
    'kind,key,expected',
    [
        ('environment', 'test', set()),
        ('server', 'db', set()),
        ('service', 'pg', set()),
        ('membership', 'test:db', {('server', 'db'), ('environment', 'test')}),
        (
            'deployment',
            'pg_db',
            {('service', 'pg'), ('server', 'db'), ('membership', 'test:db'), ('environment', 'test')},
        ),
        ('relation', 'r1', {('service', 'api'), ('service', 'pg')}),
        ('relation', 'r2', {('service', 'api')}),  # a code project is never a draft
    ],
)
def test_confirm_reach_per_kind(kind, key, expected):
    reach = rules.confirm_reach(kind, key, draft_rows())
    assert reach[0] == (kind, key) and set(reach[1:]) == expected


def test_confirm_leaves_out_what_is_confirmed_and_refuses_a_confirmed_entry():
    rows = draft_rows(confirmed={'test', 'db', 'test:db', 'pg'})
    assert rules.confirm_reach('deployment', 'pg_db', rows) == [('deployment', 'pg_db')]
    assert rules.needs('deployment', 'pg_db', rows) == []
    assert set(rules.needs('connection', 'c1', rows)) == {
        ('deployment', 'pg_db'),
        ('deployment', 'api_db'),
        ('service', 'api'),
    }
    for kind, key in [('service', 'pg'), ('membership', 'test:db')]:
        with pytest.raises(Invalid, match=f'^This {kind} is already confirmed.$'):
            rules.confirm_reach(kind, key, rows)


def test_discarding_a_service_removes_its_deployments_connections_and_relations():
    reach = rules.discard_reach('service', 'pg', draft_rows())
    assert reach[0] == ('service', 'pg')
    assert set(reach) == {('service', 'pg'), ('deployment', 'pg_db'), ('connection', 'c1'), ('relation', 'r1')}
    assert set(rules.discard_reach('service', 'mail', draft_rows())) == {('service', 'mail'), ('connection', 'c2')}


@pytest.mark.parametrize(
    'kind,key,expected',
    [
        (
            'environment',
            'test',
            {
                ('membership', 'test:db'),
                ('deployment', 'pg_db'),
                ('deployment', 'api_db'),
                ('connection', 'c1'),
                ('connection', 'c2'),
            },
        ),
        (
            'server',
            'db',
            {
                ('membership', 'test:db'),
                ('deployment', 'pg_db'),
                ('deployment', 'api_db'),
                ('connection', 'c1'),
                ('connection', 'c2'),
            },
        ),
        (
            'membership',
            'test:db',
            {('deployment', 'pg_db'), ('deployment', 'api_db'), ('connection', 'c1'), ('connection', 'c2')},
        ),
        ('deployment', 'api_db', {('connection', 'c1'), ('connection', 'c2')}),
        ('connection', 'c1', set()),
        ('relation', 'r2', set()),
    ],
)
def test_discard_reach_per_kind(kind, key, expected):
    reach = rules.discard_reach(kind, key, draft_rows())
    assert reach[0] == (kind, key) and set(reach[1:]) == expected


def test_discard_never_reaches_a_confirmed_entry():
    with pytest.raises(Invalid, match='^Only a draft can be discarded.$'):
        rules.discard_reach('service', 'pg', draft_rows(confirmed={'pg'}))
    with pytest.raises(Invalid, match='confirmed deployment'):
        rules.discard_reach('membership', 'test:db', draft_rows(confirmed={'pg_db'}))


def test_entry_names():
    rows = draft_rows()
    assert rules.entry_name('service', 'pg', rows) == 'pg'
    assert rules.entry_name('membership', 'test:db', rows) == 'db in test'
    assert rules.entry_name('deployment', 'pg_db', rows) == 'pg on db in test'
    assert rules.entry_name('connection', 'c1', rows) == 'api → pg'
    assert rules.entry_name('connection', 'c2', rows) == 'api → mail'
    assert rules.entry_name('relation', 'r1', rows) == 'api depends_on pg'
    assert rules.entry_name('relation', 'r2', rows) == 'api built_from code'


def test_discard_takes_the_draft_membership_its_deployments_leave_empty():
    def without_api_db(rows: dict) -> dict:
        del rows['deployment']['api_db'], rows['connection']['c1'], rows['connection']['c2']
        return rows

    assert set(rules.discard_reach('service', 'pg', without_api_db(draft_rows()))) == {
        ('service', 'pg'),
        ('deployment', 'pg_db'),
        ('relation', 'r1'),
        ('membership', 'test:db'),
    }
    assert ('membership', 'test:db') not in rules.discard_reach('service', 'pg', draft_rows())  # api_db is still there
    kept = without_api_db(draft_rows(confirmed={'test', 'db', 'test:db'}))
    assert set(rules.discard_reach('service', 'pg', kept)) == {
        ('service', 'pg'),
        ('deployment', 'pg_db'),
        ('relation', 'r1'),
    }
