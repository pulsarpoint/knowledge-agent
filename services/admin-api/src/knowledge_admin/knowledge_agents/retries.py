"""Run retries, as pure rules: whether a failed turn's error is transient, its short reason, the wait before each
automatic retry and the texts people see.

A turn of an event run (purpose 'event': handlers and schedules) that fails on a transient model error is retried at
most len(RETRIES) times, waiting RETRIES[n] before retry n+1; chats and every other run never retry by themselves.
Transient: connection errors, timeouts, rate limits (429) and server errors (5xx) from the OpenAI SDK, LiteLLM (whose
exceptions subclass the OpenAI SDK's) and httpx, and the builtin timeout and connection errors; an exception that
explicitly wraps one (`raise ... from`), and an exception group of only transient errors (an MCP connection). Not
transient: every other status (400, 401, 403, a too long context), the Agents SDK's own errors (max turns, an unusable
model answer, a tool error, even one caused by a connection error), configuration errors, an unreadable credential,
cancellation and anything unknown."""

import asyncio
import re
from datetime import datetime, timedelta

import httpx
import openai
from agents.exceptions import AgentsException, MaxTurnsExceeded, ModelBehaviorError
from cryptography.exceptions import InvalidTag
from litellm.exceptions import ContextWindowExceededError

from knowledge_admin.http_errors import safe_url

RETRIES = (timedelta(seconds=30), timedelta(minutes=1), timedelta(minutes=2))  # the waits before retries 1, 2 and 3
FAILURE_COMMENT = 'The agent could not answer: {reason}. Reassign the ticket or ask in Knowledge Agent to try again.'
MODEL_UNREACHABLE = 'the model could not be reached'
TOOL_SERVER_UNREACHABLE = 'a tool server could not be reached'  # an exception group: an MCP connection
URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>()\[\]]+")
UNEXPECTED = 'an unexpected error'
TIMEOUT_STATUSES = {408}


def _retryable_status(status: object) -> bool:
    return isinstance(status, int) and (status == 429 or status >= 500 or status in TIMEOUT_STATUSES)


def _status(exc: BaseException) -> int | None:
    """The HTTP status of a model error, when it has one."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    if isinstance(exc, openai.APIError):  # APIStatusError, and LiteLLM's APIError, which carries status_code
        status = getattr(exc, 'status_code', None)
        return status if isinstance(status, int) else None
    return None


def _timeout(exc: BaseException) -> bool:
    return isinstance(exc, openai.APITimeoutError | httpx.TimeoutException | TimeoutError) or _status(exc) == 408


def _connection(exc: BaseException) -> bool:
    return isinstance(exc, openai.APIConnectionError | httpx.NetworkError | httpx.RemoteProtocolError | ConnectionError)


def _cause(exc: BaseException) -> BaseException | None:
    """The transient error an unknown exception explicitly wraps (`raise ... from`), never what it was raised while
    handling; the Agents SDK's own errors (a tool error) are final whatever their cause."""
    if isinstance(exc, AgentsException | asyncio.CancelledError) or exc.__cause__ is None:
        return None
    return exc.__cause__


def _transient_error(exc: BaseException) -> BaseException | None:
    """The transient error behind `exc` (itself, the error it wraps or the first of a group of transient errors)."""
    if isinstance(exc, BaseExceptionGroup):
        found = [_transient_error(item) for item in exc.exceptions]
        return found[0] if found and all(found) else None
    if _timeout(exc) or _connection(exc):
        return exc
    status = _status(exc)
    if status is not None:
        return exc if _retryable_status(status) else None
    cause = _cause(exc)
    return None if cause is None else _transient_error(cause)


def transient(exc: BaseException) -> bool:
    """Whether a failed turn's error may pass by itself, so trying again later can help."""
    return _transient_error(exc) is not None


def _from_group(exc: BaseException | None) -> bool:
    """Whether the error is (or explicitly wraps) an exception group: an MCP server's connection, not the model's."""
    while exc is not None:
        if isinstance(exc, BaseExceptionGroup):
            return True
        exc = _cause(exc)
    return False


def masked(text: str) -> str:
    """Free text made safe to show outside: one line, every URL without its userinfo, query and fragment."""
    return URL.sub(lambda found: safe_url(found.group(0)), ' '.join(text.split()))


def reason(exc: BaseException) -> str:
    """A failure in a few words, safe for an external Jira comment: a fixed label for every error, except a
    configuration error (ValueError), whose text is masked (masked(); the session's credentials are redacted by the
    caller). The retry status message says the same."""
    if transient(exc):
        return TOOL_SERVER_UNREACHABLE if _from_group(exc) else MODEL_UNREACHABLE
    if isinstance(exc, ContextWindowExceededError):
        return 'the conversation is too long for the model'
    if isinstance(exc, MaxTurnsExceeded):
        return 'it used all its turns'
    if isinstance(exc, ModelBehaviorError):
        return 'the model returned an answer it could not use'
    if isinstance(exc, InvalidTag):
        return 'a configured credential could not be decrypted'
    status = _status(exc)
    if status is not None:
        return f'the model refused the request ({status})'
    if isinstance(exc, ValueError) and str(exc).strip():  # configuration: a disabled agent or model, a missing key
        return masked(str(exc))[:200].rstrip('.')
    return f'{UNEXPECTED} ({type(exc).__name__})'


def retry_wait(purpose: str | None, retries: int, exc: BaseException) -> timedelta | None:
    """How long to wait before retrying a failed turn that has been retried `retries` times; None: it is not retried
    (not an event run, not a transient error, or no retry left)."""
    if purpose != 'event' or retries >= len(RETRIES) or not transient(exc):
        return None
    return RETRIES[retries]


def retry_message(attempt: int, at: datetime, why: str) -> str:
    """The status message of a run waiting for retry `attempt` at `at`:
    'Retry 2 of 3 at 10:42 UTC: the model could not be reached'."""
    return f'Retry {attempt} of {len(RETRIES)} at {at.strftime("%H:%M")} UTC: {why}'


def failure_comment(why: str) -> str:
    """The Jira comment of an event run that could not answer."""
    return FAILURE_COMMENT.format(reason=why.strip() or UNEXPECTED)
