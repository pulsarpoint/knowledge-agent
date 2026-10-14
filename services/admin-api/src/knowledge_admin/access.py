"""Identity at the private API boundary behind the administrator gateway."""

from fastapi import HTTPException, Request


def request_actor(request: Request) -> tuple[str, str]:
    subject = request.headers.get('x-forwarded-user', '')
    if not subject:
        raise HTTPException(403, 'Authenticated user identity required')
    return subject, request.headers.get('x-forwarded-email', '')
