"""The pure rules of run retries: which failures are transient (the classifier, for every error class that reaches a
session turn: the OpenAI SDK, LiteLLM, which subclasses it, httpx and the builtins), their short reason, the wait
before each retry and the status message."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import litellm.exceptions as litellm_errors
import openai
import pytest
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError, UserError
from cryptography.exceptions import InvalidTag
from knowledge_admin.knowledge_agents.retries import (
    RETRIES,
    failure_comment,
    reason,
    retry_message,
    retry_wait,
    transient,
)

REQUEST = httpx.Request('POST', 'http://model.test/v1/responses')


def status_error(cls, status: int):
    """An OpenAI SDK status error as the client raises it for a response with `status`."""
    return cls(f'Error code: {status}', response=httpx.Response(status, request=REQUEST), body=None)


def http_status(status: int) -> httpx.HTTPStatusError:
    response = httpx.Response(status, request=REQUEST)
    return httpx.HTTPStatusError(f'{status}', request=REQUEST, response=response)


def wrapped(cause: BaseException) -> RuntimeError:
    try:
        raise RuntimeError('the model call failed') from cause
    except RuntimeError as exc:
        return exc


TRANSIENT = {
    'openai connection error': openai.APIConnectionError(request=REQUEST),
    'openai timeout': openai.APITimeoutError(request=REQUEST),
    'openai 429': status_error(openai.RateLimitError, 429),
    'openai 500': status_error(openai.InternalServerError, 500),
    'openai 502': status_error(openai.APIStatusError, 502),
    'openai 503': status_error(openai.InternalServerError, 503),
    'openai 408': status_error(openai.APIStatusError, 408),
    'litellm connection error': litellm_errors.APIConnectionError('down', 'anthropic', 'claude'),
    'litellm timeout': litellm_errors.Timeout('slow', 'claude', 'anthropic'),
    'litellm rate limit': litellm_errors.RateLimitError('busy', 'anthropic', 'claude'),
    'litellm service unavailable': litellm_errors.ServiceUnavailableError('down', 'anthropic', 'claude'),
    'litellm internal server error': litellm_errors.InternalServerError('oops', 'anthropic', 'claude'),
    'litellm bad gateway': litellm_errors.BadGatewayError('gateway', 'anthropic', 'claude'),
    'litellm api error 500': litellm_errors.APIError(500, 'oops', 'anthropic', 'claude'),
    'httpx connect error': httpx.ConnectError('refused', request=REQUEST),
    'httpx read timeout': httpx.ReadTimeout('slow', request=REQUEST),
    'httpx remote protocol error': httpx.RemoteProtocolError('dropped', request=REQUEST),
    'httpx 503': http_status(503),
    'httpx 429': http_status(429),
    'builtin timeout': TimeoutError(),
    'connection reset': ConnectionResetError(),
    'wrapped connection error': wrapped(httpx.ConnectError('refused', request=REQUEST)),
    'group of connection errors': ExceptionGroup('mcp', [httpx.ConnectError('refused', request=REQUEST)]),
}

FINAL = {
    'openai 401': status_error(openai.AuthenticationError, 401),
    'openai 403': status_error(openai.PermissionDeniedError, 403),
    'openai 400': status_error(openai.BadRequestError, 400),
    'openai 404': status_error(openai.NotFoundError, 404),
    'openai 422': status_error(openai.UnprocessableEntityError, 422),
    'litellm auth': litellm_errors.AuthenticationError('key', 'anthropic', 'claude'),
    'litellm permission': litellm_errors.PermissionDeniedError(
        'no', 'anthropic', 'claude', response=httpx.Response(403, request=REQUEST)
    ),
    'litellm bad request': litellm_errors.BadRequestError('bad', 'claude', 'anthropic'),
    'litellm context window': litellm_errors.ContextWindowExceededError('too long', 'claude', 'anthropic'),
    'litellm content policy': litellm_errors.ContentPolicyViolationError('no', 'claude', 'anthropic'),
    'litellm api error 400': litellm_errors.APIError(400, 'bad', 'anthropic', 'claude'),
    'httpx 401': http_status(401),
    'httpx unsupported protocol': httpx.UnsupportedProtocol('ftp', request=REQUEST),
    'max turns': MaxTurnsExceeded('Max turns (20) exceeded'),
    'model behavior': ModelBehaviorError('bad tool call'),
    'tool error wrapping a connection error': UserError('Error running tool ssh_run: refused'),
    'configuration': ValueError('This agent is disabled.'),
    'unreadable credential': InvalidTag(),
    'cancelled': asyncio.CancelledError(),
    'runtime error': RuntimeError('boom'),
    'mixed group': ExceptionGroup('mcp', [httpx.ConnectError('refused'), ValueError('bad')]),
}


@pytest.mark.parametrize('name', TRANSIENT)
def test_transient_failures(name):
    assert transient(TRANSIENT[name]) is True


@pytest.mark.parametrize('name', FINAL)
def test_failures_that_are_not_transient(name):
    assert transient(FINAL[name]) is False


def test_a_tool_error_is_final_even_when_its_cause_is_a_connection_error():
    try:
        raise UserError('Error running tool shell_run: refused') from httpx.ConnectError('refused')
    except UserError as exc:
        assert transient(exc) is False


def test_an_error_raised_while_handling_a_connection_error_is_not_transient():
    try:
        try:
            raise httpx.ConnectError('refused')
        except httpx.ConnectError:
            raise RuntimeError('cleanup failed')  # its __context__ only, not its cause
    except RuntimeError as exc:
        assert transient(exc) is False


MODEL = 'the model could not be reached'
TOOL_SERVER = 'a tool server could not be reached'


@pytest.mark.parametrize(
    'exc,expected',
    [
        (openai.APIConnectionError(request=REQUEST), MODEL),
        (openai.APITimeoutError(request=REQUEST), MODEL),
        (litellm_errors.Timeout('slow', 'claude', 'anthropic'), MODEL),
        (status_error(openai.RateLimitError, 429), MODEL),
        (status_error(openai.InternalServerError, 503), MODEL),
        (http_status(502), MODEL),
        (httpx.ConnectError('refused'), MODEL),
        (TimeoutError(), MODEL),
        (wrapped(openai.APITimeoutError(request=REQUEST)), MODEL),
        (ExceptionGroup('mcp', [httpx.ConnectError('refused', request=REQUEST)]), TOOL_SERVER),
        (status_error(openai.AuthenticationError, 401), 'the model refused the request (401)'),
        (
            litellm_errors.ContextWindowExceededError('too long', 'claude', 'anthropic'),
            'the conversation is too long for the model',
        ),
        (MaxTurnsExceeded('Max turns'), 'it used all its turns'),
        (ModelBehaviorError('bad'), 'the model returned an answer it could not use'),
        (ValueError('This agent is disabled.'), 'This agent is disabled'),
        (InvalidTag(), 'a configured credential could not be decrypted'),
        (RuntimeError('boom https://u:tok@host/x'), 'an unexpected error (RuntimeError)'),
    ],
)
def test_the_short_reason_is_a_fixed_label(exc, expected):
    assert reason(exc) == expected


@pytest.mark.parametrize(
    'text',
    [
        'Bad base URL https://u:tok@host/x?key=k for the model',
        "Bad base URL 'https://u:tok@host/x?key=k'.",
        'Bad base URL (http://u:tok@host/x#key=k)',
    ],
)
def test_a_configuration_error_never_leaks_a_url_with_credentials(text):
    found = reason(ValueError(text))
    assert 'tok' not in found and 'key=k' not in found
    assert 'host/x' in found and '***' in found


def test_three_retries_up_to_two_minutes():
    assert RETRIES == (timedelta(seconds=30), timedelta(minutes=1), timedelta(minutes=2))


def test_the_wait_before_each_retry_of_an_event_run():
    error = openai.APIConnectionError(request=REQUEST)
    assert [retry_wait('event', retries, error) for retries in range(4)] == [*RETRIES, None]


@pytest.mark.parametrize('purpose', ['chat', 'documents', 'revision', 'resource'])
def test_other_runs_never_retry_automatically(purpose):
    assert retry_wait(purpose, 0, openai.APIConnectionError(request=REQUEST)) is None


def test_a_final_failure_is_not_retried():
    assert retry_wait('event', 0, status_error(openai.AuthenticationError, 401)) is None


def test_the_status_message_while_waiting():
    at = datetime(2026, 10, 28, 10, 42, 5, tzinfo=UTC)
    assert retry_message(2, at, 'connection error') == 'Retry 2 of 3 at 10:42 UTC: connection error'


def test_the_jira_failure_comment():
    assert failure_comment('the model could not be reached') == (
        'The agent could not answer: the model could not be reached. '
        'Reassign the ticket or ask in Knowledge Agent to try again.'
    )
    assert failure_comment('') == (
        'The agent could not answer: an unexpected error. Reassign the ticket or ask in Knowledge Agent to try again.'
    )
