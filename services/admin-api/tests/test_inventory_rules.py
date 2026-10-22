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
