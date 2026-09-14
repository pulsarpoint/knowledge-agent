"""HTTP client for repository inventory and the placeholder code question route."""
from __future__ import annotations

from urllib.parse import quote

import httpx

from docs_wiki.rag_client import _SyncBridgeTransport, _error_detail


class CodeAgentError(Exception):
    pass


class CodeAgentClient:
    def __init__(self, base_url: str,
                transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None):
        if transport is not None and not isinstance(transport, httpx.BaseTransport):
            transport = _SyncBridgeTransport(transport)
        self._client = httpx.Client(base_url=base_url,
                                    timeout=10.0,
                                    transport=transport)

    def repos(self) -> list[dict]:
        try:
            response = self._client.get("/repos")
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise CodeAgentError(f"repos failed: {_error_detail(exc)}") from exc

    def ask(self, repo: str, question: str, context: str | None = None) -> str:
        payload = {"question": question}
        if context is not None:
            payload["context"] = context
        try:
            response = self._client.post(f"/repos/{quote(repo, safe='')}/ask", json=payload)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            raise CodeAgentError(f"ask failed: {_error_detail(exc)}") from exc

    def close(self) -> None:
        self._client.close()
