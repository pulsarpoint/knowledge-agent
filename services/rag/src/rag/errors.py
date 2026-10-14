"""Consistent JSON error responses for the retrieval and workspace APIs."""

from __future__ import annotations

from fastapi.responses import JSONResponse


def error_response(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={'error': {'message': message}})
