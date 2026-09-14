"""Thin synchronous HTTP client for the standalone RAG service
(services/rag): one method per endpoint, zero domain logic — no grouping,
freshness, or presentation decisions belong here (callers own that).
Structure mirrors llm_client.py: sync httpx.Client, injectable transport
for tests. `_SyncBridgeTransport` and `_error_detail` live here (the
single definition); llm_client.py imports both back from this module."""
from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx


class RagError(Exception):
    pass


class RagNotFound(RagError):
    pass


def _error_detail(exc: Exception) -> str:
    """The status line alone ("400 Bad Request") hides the server's actual
    complaint (wrong model name, unsupported parameter, ...); append the
    response body so banners and logs say why."""
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text.strip()
        if body:
            return f"{exc} — server said: {body[:300]}"
    return str(exc)


class _SyncBridgeTransport(httpx.BaseTransport):
    """Adapts an async-only transport (e.g. httpx.ASGITransport, which
    implements only handle_async_request) for use by a synchronous
    httpx.Client, via a private event loop. This keeps RagClient's (and
    LlmClient's) public methods fully synchronous even when a fake ASGI
    test endpoint is wired in through `transport=`."""

    def __init__(self, async_transport: httpx.AsyncBaseTransport):
        self._async_transport = async_transport
        self._closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def run() -> httpx.Response:
            response = await self._async_transport.handle_async_request(request)
            await response.aread()
            return response

        response = asyncio.run(run())
        # Rebuild as a sync Response: the original's .stream is an
        # AsyncByteStream (from handle_async_request), which fails the
        # SyncByteStream assertion httpx.Client makes on the way out.
        # Passing already-read bytes via content= gives us httpx.ByteStream,
        # which satisfies both stream protocols.
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )

    def close(self) -> None:
        # A caller may share one bridge instance between two httpx.Client
        # objects (e.g. LlmClient previously shared it between _chat and
        # _embed), so httpx.Client.close() can call this more than once.
        # Idempotent regardless of whether the wrapped async transport
        # tolerates a second aclose().
        if self._closed:
            return
        self._closed = True
        asyncio.run(self._async_transport.aclose())


def _quote_path(value: str) -> str:
    """Percent-encode each `/`-delimited segment individually so a
    multi-segment document path (e.g. "how-to/a.md") survives the round
    trip: a single quote() over the whole string would turn the literal
    "/" separators into "%2F", which the server's {path:path} route
    would not treat as segment boundaries."""
    return "/".join(quote(segment, safe="") for segment in value.split("/"))


class RagClient:
    def __init__(self, base_url: str,
                transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None):
        if transport is not None and not isinstance(transport, httpx.BaseTransport):
            transport = _SyncBridgeTransport(transport)
        self._client = httpx.Client(base_url=base_url, timeout=10.0,
                                    transport=transport)

    def list_documents(self, collection: str) -> list[dict]:
        return self._request(
            "GET", f"/collections/{_quote_path(collection)}/documents")

    def get_document(self, collection: str, path: str) -> dict:
        url = (f"/collections/{_quote_path(collection)}/documents/"
              f"{_quote_path(path)}")
        try:
            response = self._client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise RagNotFound(
                    f"document not found: {_error_detail(exc)}") from exc
            raise RagError(
                f"get_document failed: {_error_detail(exc)}") from exc
        except Exception as exc:
            raise RagError(
                f"get_document failed: {_error_detail(exc)}") from exc

    def search(self, collection: str, query: str, top: int = 8) -> list[dict]:
        return self._request(
            "POST", f"/collections/{_quote_path(collection)}/search",
            json={"query": query, "top": top})

    def collections(self) -> list[dict]:
        """GET /collections — every configured collection this RAG service
        knows about, each row carrying name/kind/retrieval keys. Zero
        domain logic here, same as every other method on this client:
        grouping collections by kind (documentation vs. code cards) is a caller
        decision."""
        return self._request("GET", "/collections")

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, **kwargs) -> list[dict]:
        try:
            response = self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise RagError(f"request failed: {_error_detail(exc)}") from exc
