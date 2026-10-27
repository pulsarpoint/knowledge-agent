"""Every error response is logged once (knowledge_admin.http) with the request id; validation errors name the field;
the rejected input is logged only at debug level, masked when it looks like a secret."""

import asyncio
import logging
from uuid import UUID

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from knowledge_admin import logs
from knowledge_admin.app import create_app
from knowledge_admin.config import App, Config, ServerConfig
from fastapi.responses import StreamingResponse
from knowledge_admin.http_errors import RequestIdMiddleware, escape, mask, request_id, safe_url, validation_text
from knowledge_admin.llm.mcp_api import MCPInput
from knowledge_admin.repositories import RepositoryInput
from knowledge_admin.skills.api import GitSource
from pydantic import BaseModel
from starlette.requests import ClientDisconnect

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

    @app.post('/test/mcp')
    def mcp(value: MCPInput):
        return {'ok': True}

    @app.post('/test/repository')
    def repository(value: RepositoryInput):
        return {'ok': True}

    @app.post('/test/skill')
    def skill(value: GitSource):
        return {'ok': True}

    @app.get('/test/named/{name}')
    def named(name: str):
        raise HTTPException(400, f'No agent named {name}.')

    @app.post('/test/gone')
    def gone():
        raise HTTPException(400, 'There was an error parsing the body') from ClientDisconnect()

    @app.get('/test/stream')
    def stream():
        def chunks():
            yield b'first'
            raise RuntimeError('broke mid-stream')

        return StreamingResponse(chunks())

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


def test_safe_url_hides_userinfo_query_and_fragment_and_keeps_the_rest():
    assert safe_url('https://user:ghp_x@github.com/o/r.git') == 'https://***@github.com/o/r.git'
    assert safe_url('https://mcp.test:8443/sse?api_key=k&x=1') == 'https://mcp.test:8443/sse?***'
    assert safe_url('https://u@h/p?q=1#frag') == 'https://***@h/p?***#***'
    for kept in ('https://github.com/o/r.git', 'code', 'not a url', 'mailto:a@b?subject=x', ''):
        assert safe_url(kept) == kept


def test_mask_hides_credentials_in_urls_nested_too():
    assert mask('https://user:ghp_x@github.com/o/r', ('body', 'remote_url')) == "'https://***@github.com/o/r'"
    assert mask({'url': 'https://mcp/x?api_key=k', 'name': 'n'}, ('body',)) == (
        "{'url': 'https://mcp/x?***', 'name': 'n'}"
    )
    assert mask(['https://a:b@h/p'], ('body', 'urls')) == "['https://***@h/p']"


@pytest.mark.parametrize(
    'path,body,secret,shown',
    [
        ('/test/mcp', {'name': 'm', 'url': 'https://mcp.test/sse?api_key=sk-live-1'}, 'sk-live-1', 'mcp.test/sse?***'),
        (
            '/test/repository',
            {'remote_url': 'https://bob:ghp_repo@github.com/o/r.git'},
            'ghp_repo',
            'https://***@github.com/o/r.git',
        ),
        (
            '/test/skill',
            {'url': 'https://bob:ghp_skill@github.com/o/skills.git' + '/x' * 1000},
            'ghp_skill',
            'https://***@github.com/o/skills.git',
        ),
    ],
)
def test_rejected_urls_with_credentials_are_masked_at_debug(caplog, path, body, secret, shown):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    response = client().post(path, json=body)
    assert response.status_code == 400, response.text
    assert secret not in caplog.text and secret not in response.text
    assert shown in records(caplog)[0].getMessage()


def test_escape_makes_control_characters_visible():
    assert escape('a\nERROR forged\r\tx\x1b\x00') == 'a\\nERROR forged\\r\\tx\\x1b\\x00'
    assert escape('plain é text') == 'plain é text'


def test_a_newline_in_a_detail_cannot_forge_a_log_line(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client().get('/test/named/a%0AERROR forged', headers={'X-Request-ID': 'r6'})
    assert response.json() == {'error': {'message': 'No agent named a\nERROR forged.'}}  # the response is unchanged
    [record] = records(caplog)
    assert record.getMessage().endswith(' -> 400 No agent named a\\nERROR forged.')
    assert '\n' not in record.getMessage()


def test_a_newline_in_an_extra_field_name_cannot_forge_a_log_line(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    client().post('/test/mcp', json={'name': 'm', 'url': 'https://mcp.test/sse', 'a\nERROR forged': 'v\r\n'})
    [record] = records(caplog)
    assert '\n' not in record.getMessage() and '\r' not in record.getMessage()
    assert 'a\\nERROR forged: Extra inputs are not permitted (extra_forbidden)' in record.getMessage()


def test_a_failure_after_the_response_started_is_one_line_without_a_second_traceback(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    with pytest.raises(RuntimeError):  # re-raised: uvicorn logs the traceback once
        client().get('/test/stream', headers={'X-Request-ID': 'r7'})
    [record] = records(caplog)
    assert record.levelno == logging.ERROR and record.exc_info is None
    assert record.getMessage() == 'request r7 GET /test/stream -> 200 failed during the response'


def test_a_client_disconnect_is_info(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    client().post('/test/gone', headers={'X-Request-ID': 'r8'})
    [record] = records(caplog)
    assert record.levelno == logging.INFO
    assert record.getMessage() == 'request r8 POST /test/gone -> client disconnected'

    async def app(scope, receive, send):
        raise ClientDisconnect()

    sent = []

    async def send(message):
        sent.append(message)

    caplog.clear()
    scope = {'type': 'http', 'method': 'POST', 'path': '/x', 'headers': [(b'x-request-id', b'r9')], 'query_string': b''}
    asyncio.run(RequestIdMiddleware(app)(scope, None, send))
    assert sent == []  # nobody is listening
    assert [(r.levelno, r.getMessage()) for r in records(caplog)] == [
        (logging.INFO, 'request r9 POST /x -> client disconnected')
    ]


def test_an_unknown_route_is_info_and_a_missing_record_is_a_warning(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    c = client()
    assert c.get('/test/nowhere', headers={'X-Request-ID': 'r10'}).status_code == 404
    assert c.get('/test/missing', headers={'X-Request-ID': 'r11'}).status_code == 404
    assert [(r.levelno, r.getMessage()) for r in records(caplog)] == [
        (logging.INFO, 'request r10 GET /test/nowhere -> 404 Not Found'),
        (logging.WARNING, 'request r11 GET /test/missing -> 404 Agent not found'),
    ]
