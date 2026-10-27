"""admin-api error responses: one log line per error (knowledge_admin.http) with the request id, and validation
messages that name the field. Inputs are logged at debug level only, masked when they look like secrets (secret-looking
names, credentials and queries in URLs); never headers or cookies. Control characters in a line are escaped, so a
client cannot forge log lines."""

import contextvars
import logging
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

logger = logging.getLogger('knowledge_admin.http')
current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar('request_id', default='-')

SAFE_ID = re.compile(r'[A-Za-z0-9._-]{1,64}')
SECRET = re.compile(r'token|password|secret|key|authorization|credential', re.IGNORECASE)
LOCATIONS = ('body', 'query', 'path')
NEVER_LOGGED = ('header', 'cookie')
MAX_INPUT = 200
CONTROL = re.compile(r'[\x00-\x1f\x7f]')


def request_id(incoming: str | None) -> str:
    """The incoming X-Request-ID when it is a short safe token, otherwise a new one."""
    return incoming if incoming and SAFE_ID.fullmatch(incoming) else uuid4().hex


def escape(text: str) -> str:
    """Control characters (newlines, carriage returns, other C0 characters) as visible escapes: one line stays one."""
    return CONTROL.sub(lambda match: match.group().encode('unicode_escape').decode(), text)


def safe_url(text: str) -> str:
    """'https://user:token@host/p?key=k' -> 'https://***@host/p?***': a URL keeps its scheme, host and path; its
    userinfo, query and fragment are hidden. Any other text is returned as it is."""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    userinfo = '@' in parts.netloc
    if not (parts.scheme and parts.netloc) or not (userinfo or parts.query or parts.fragment):
        return text
    host = parts.netloc.rpartition('@')[2]
    return (
        f'{parts.scheme}://{"***@" if userinfo else ""}{host}{parts.path}'
        f'{"?***" if parts.query else ""}{"#***" if parts.fragment else ""}'
    )


def _masked(value: Any) -> Any:
    if isinstance(value, str):
        return safe_url(value)
    if isinstance(value, dict):
        return {k: '***' if SECRET.search(str(k)) else _masked(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_masked(v) for v in value]
    return value


def mask(value: Any, loc: tuple | list) -> str:
    """The rejected input as log text, at most MAX_INPUT characters: *** when its location names a secret (or it is
    a header or cookie), every nested key that names one masked, and credentials and queries in URLs hidden."""
    if (loc and loc[0] in NEVER_LOGGED) or any(SECRET.search(str(part)) for part in loc):
        return '***'
    return repr(_masked(value))[:MAX_INPUT]


def validation_text(errors: list[dict], *, types: bool = False, inputs: bool = False) -> str:
    """'expert_ids.2: Input should be a valid UUID; ...': the location without body/query/path, then the message;
    with the error type and the masked input for the log."""
    parts = []
    for error in errors:
        loc = list(error.get('loc', ()))
        field = '.'.join(str(part) for part in (loc[1:] if loc and loc[0] in LOCATIONS else loc))
        text = f'{field}: {error["msg"]}' if field else error['msg']
        if types:
            text += f' ({error["type"]})'
        if inputs:
            text += f' input={mask(error.get("input"), loc)}'
        parts.append(text)
    return '; '.join(parts)


def _line(request: Request, outcome: str) -> str:
    """'request <id> <METHOD> <path> -> <outcome>', without the query string, control characters escaped."""
    return escape(f'request {current_request_id.get()} {request.method} {request.url.path} -> {outcome}')


def install(app: FastAPI) -> None:
    """The error handlers and the request-id middleware."""

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        # A dict detail carries a code the UI recognises beside the message, e.g. {"message": ..., "code": "STALE_BASE"}.
        error = exc.detail if isinstance(exc.detail, dict) else {'message': str(exc.detail)}
        outcome = f'{exc.status_code} {error.get("message", "")}'
        if error.get('code'):
            outcome += f' ({error["code"]})'
        if isinstance(exc.__cause__, ClientDisconnect):  # FastAPI's 400 when the client left while sending the body
            logger.info(_line(request, 'client disconnected'))
        elif exc.status_code == 404 and 'endpoint' not in request.scope:  # no route matched: scanners and typos
            logger.info(_line(request, outcome))
        elif exc.status_code >= 500:
            logger.error(_line(request, outcome), exc_info=exc)
        elif exc.status_code >= 400:
            logger.warning(_line(request, outcome))
        return JSONResponse(status_code=exc.status_code, content={'error': error})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Validation inputs may contain a GitHub token: the response has messages only, never inputs or context.
        errors = list(exc.errors())
        debug = logger.isEnabledFor(logging.DEBUG)
        logger.warning(_line(request, '400 ' + validation_text(errors, types=True, inputs=debug)))
        return JSONResponse(status_code=400, content={'error': {'message': validation_text(errors)}})

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc: psycopg.Error):
        message = 'Administration database unavailable'
        logger.error(_line(request, f'503 {message}'), exc_info=exc)
        return JSONResponse(status_code=503, content={'error': {'message': message}})

    # Keep it last: a middleware added after it sits outside it, and its errors would have no request id.
    app.add_middleware(RequestIdMiddleware)


class RequestIdMiddleware:
    """Gives every request an id (X-Request-ID in and out, on errors too) for the log lines, and answers an unhandled
    exception with a logged 500 inside it, so that answer carries the id as well."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        rid = request_id(dict(scope['headers']).get(b'x-request-id', b'').decode('latin-1'))
        token = current_request_id.set(rid)
        sent_status = None

        async def send_with_id(message):
            nonlocal sent_status
            if message['type'] == 'http.response.start':
                sent_status = message['status']
                MutableHeaders(scope=message).append('X-Request-ID', rid)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except ClientDisconnect:  # the client left: nobody to answer, nothing wrong here
            logger.info(_line(Request(scope), 'client disconnected'))
        except Exception:
            if sent_status is not None:  # too late for a 500; uvicorn logs the traceback of the re-raised exception
                logger.error(_line(Request(scope), f'{sent_status} failed during the response'))
                raise
            logger.exception(_line(Request(scope), '500 Internal error'))
            response = JSONResponse(status_code=500, content={'error': {'message': 'Internal error'}})
            await response(scope, receive, send_with_id)
        finally:
            current_request_id.reset(token)
