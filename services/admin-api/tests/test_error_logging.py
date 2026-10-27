"""Every error response is logged once (knowledge_admin.http) with the request id; validation errors name the field;
the rejected input is logged only at debug level, masked when it looks like a secret."""

import logging
from uuid import UUID

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from knowledge_admin import logs
from knowledge_admin.app import create_app
from knowledge_admin.config import App, Config, ServerConfig
from knowledge_admin.http_errors import mask, request_id, validation_text
from pydantic import BaseModel

LOGGER = 'knowledge_admin.http'


class Body(BaseModel):
    expert_ids: list[UUID] = []
    github_token: str = ''
    settings: dict = {}


def client() -> TestClient:
    app = create_app(Config(apps=[App(name='shop')]), server=ServerConfig())

    @app.get('/test/conflict')
    def conflict():
        raise HTTPException(409, {'message': 'Someone saved first.', 'code': 'STALE_BASE'})

    @app.get('/test/missing')
    def missing():
        raise HTTPException(404, 'Agent not found')

    @app.get('/test/upstream')
    def upstream():
        raise HTTPException(502, 'Jira did not answer')

    @app.get('/test/database')
    def database():
        raise psycopg.OperationalError('connection refused')

    @app.get('/test/boom')
    def boom():
        raise RuntimeError('unexpected')

    @app.post('/test/body')
    def body(value: Body):
        return {'ok': True}

    return TestClient(app)


def records(caplog, level=None):
    return [r for r in caplog.records if r.name == LOGGER and (level is None or r.levelno == level)]


def test_a_4xx_is_one_warning_with_the_request_id_method_path_status_message_and_code(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client().get('/test/conflict?token=abc', headers={'X-Request-ID': 'req-42'})
    assert response.status_code == 409
    assert response.json() == {'error': {'message': 'Someone saved first.', 'code': 'STALE_BASE'}}  # unchanged
    assert response.headers['X-Request-ID'] == 'req-42'
    [record] = records(caplog)
    assert record.levelno == logging.WARNING
    assert record.getMessage() == 'request req-42 GET /test/conflict -> 409 Someone saved first. (STALE_BASE)'
    assert 'abc' not in caplog.text  # no query string


def test_a_plain_detail_has_no_code(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client().get('/test/missing', headers={'X-Request-ID': 'r1'})
    assert response.json() == {'error': {'message': 'Agent not found'}}
    assert [r.getMessage() for r in records(caplog)] == ['request r1 GET /test/missing -> 404 Agent not found']


def test_a_5xx_http_error_is_an_error_with_the_traceback(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client().get('/test/upstream', headers={'X-Request-ID': 'r2'})
    assert response.status_code == 502 and response.json() == {'error': {'message': 'Jira did not answer'}}
    [record] = records(caplog)
    assert record.levelno == logging.ERROR and record.exc_info is not None
    assert record.getMessage() == 'request r2 GET /test/upstream -> 502 Jira did not answer'


def test_a_database_error_still_answers_503_and_is_logged_with_the_traceback(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client().get('/test/database', headers={'X-Request-ID': 'r3'})
    assert response.status_code == 503
    assert response.json() == {'error': {'message': 'Administration database unavailable'}}
    assert response.headers['X-Request-ID'] == 'r3'
    [record] = records(caplog)
    assert record.levelno == logging.ERROR and record.exc_info[0] is psycopg.OperationalError
    assert record.getMessage() == 'request r3 GET /test/database -> 503 Administration database unavailable'


def test_an_unhandled_exception_answers_500_with_the_request_id_and_logs_the_traceback(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client().get('/test/boom', headers={'X-Request-ID': 'r4'})
    assert response.status_code == 500
    assert response.json() == {'error': {'message': 'Internal error'}}
    assert response.headers['X-Request-ID'] == 'r4'
    [record] = records(caplog)
    assert record.levelno == logging.ERROR and record.exc_info[0] is RuntimeError
    assert record.getMessage() == 'request r4 GET /test/boom -> 500 Internal error'


def test_a_validation_error_names_the_field_in_the_response_and_the_log(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    bad = {'expert_ids': [str(UUID(int=1)), str(UUID(int=2)), 'code'], 'github_token': 5}
    response = client().post('/test/body', json=bad, headers={'X-Request-ID': 'r5'})
    assert response.status_code == 400
    message = response.json()['error']['message']
    assert message.startswith('expert_ids.2: Input should be a valid UUID')
    assert '; github_token: Input should be a valid string' in message
    [record] = records(caplog)
    assert record.levelno == logging.WARNING
    line = record.getMessage()
    assert line.startswith('request r5 POST /test/body -> 400 expert_ids.2: Input should be a valid UUID')
    assert '(uuid_parsing); github_token: Input should be a valid string' in line and line.endswith('(string_type)')
    assert "'code'" not in line and 'input=' not in line  # no inputs at info level


def test_at_debug_level_the_rejected_input_is_logged_masked(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    bad = {'expert_ids': ['code'], 'github_token': 5, 'settings': []}
    response = client().post('/test/body', json=bad)
    assert 'input=' not in response.json()['error']['message']  # never in the response
    line = records(caplog)[0].getMessage()
    assert 'expert_ids.0: Input should be a valid UUID' in line and "input='code'" in line
    assert 'github_token: Input should be a valid string' in line and 'input=***' in line and 'input=5' not in line
    assert 'settings: Input should be a valid dictionary (dict_type) input=[]' in line


def test_every_response_gets_a_request_id_and_unsafe_ones_are_replaced():
    c = client()
    fresh = c.get('/health').headers['X-Request-ID']
    assert len(fresh) == 32 and int(fresh, 16) >= 0
    assert c.get('/health', headers={'X-Request-ID': 'abc-123'}).headers['X-Request-ID'] == 'abc-123'
    unsafe = c.get('/health', headers={'X-Request-ID': 'evil value\twith spaces'}).headers['X-Request-ID']
    assert unsafe != 'evil value\twith spaces' and len(unsafe) == 32


def test_request_id_keeps_short_safe_tokens_only():
    assert request_id('a.b_c-1') == 'a.b_c-1'
    for unsafe in (None, '', 'x' * 65, 'has space', 'semi;colon', 'new\nline'):
        assert len(request_id(unsafe)) == 32


def test_validation_text_joins_the_fields_without_the_location_kind():
    errors = [
        {'loc': ('body', 'expert_ids', 2), 'msg': 'Input should be a valid UUID', 'type': 'uuid_parsing', 'input': 'x'},
        {'loc': ('query', 'limit'), 'msg': 'Input should be a valid integer', 'type': 'int_parsing', 'input': 'y'},
        {'loc': ('body',), 'msg': 'Value error, Enter a message', 'type': 'value_error', 'input': {}},
    ]
    assert validation_text(errors) == (
        'expert_ids.2: Input should be a valid UUID; limit: Input should be a valid integer; '
        'Value error, Enter a message'
    )
    assert validation_text(errors, types=True).endswith('Value error, Enter a message (value_error)')
    assert validation_text(errors[:1], types=True, inputs=True) == (
        "expert_ids.2: Input should be a valid UUID (uuid_parsing) input='x'"
    )


def test_mask_hides_secret_looking_locations_and_keys_and_shortens():
    assert mask('ghp_123', ('body', 'github_token')) == '***'
    assert mask('x', ('body', 'Password')) == '***'
    assert mask('x', ('header', 'x-forwarded-groups')) == '***'  # never headers or cookies
    assert mask('x', ('cookie', 'session')) == '***'
    assert mask({'name': 'n', 'API_KEY': 'k', 'nested': [{'Authorization': 'Bearer t'}]}, ('body',)) == (
        "{'name': 'n', 'API_KEY': '***', 'nested': [{'Authorization': '***'}]}"
    )
    assert mask({'credentials': {'user': 'u'}}, ('body', 'x')) == "{'credentials': '***'}"
    assert mask('code', ('body', 'expert_ids', 0)) == "'code'"
    assert mask('a' * 500, ('body', 'name')) == ("'" + 'a' * 500)[:200]


def test_the_log_level_is_one_of_three_names(monkeypatch):
    assert logs.parse_level(None) == 'info'
    assert logs.parse_level('DEBUG') == 'debug'
    assert logs.parse_level(' Warning ') == 'warning'
    with pytest.raises(ValueError, match='LOG_LEVEL must be debug, info or warning, not "verbose"'):
        logs.parse_level('verbose')


def test_configure_sets_the_application_loggers_and_the_format():
    app_logger = logging.getLogger('knowledge_admin')
    root = logging.getLogger()
    saved = app_logger.level, root.level, root.handlers[:]
    try:
        assert logs.configure('debug') == 'debug'
        assert logging.getLogger('knowledge_admin.http').isEnabledFor(logging.DEBUG)
        assert root.level == logging.INFO  # libraries stay at info: their debug output can carry secrets
        formatter = root.handlers[-1].formatter
        assert formatter._fmt == logs.FORMAT and '%(asctime)s' in logs.FORMAT and '%(name)s' in logs.FORMAT
        logs.configure('warning')
        assert app_logger.level == logging.WARNING and root.level == logging.WARNING
    finally:
        app_logger.setLevel(saved[0])
        root.setLevel(saved[1])
        root.handlers[:] = saved[2]
