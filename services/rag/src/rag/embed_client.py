"""OpenAI-compatible embeddings client with injectable transport."""
from __future__ import annotations

import asyncio

import httpx

from rag.config import EmbedConfig

EMBED_BATCH = 64


class EmbedError(Exception):
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
    httpx.Client, via a private event loop. This keeps EmbedClient's public
    methods fully synchronous even when the fake ASGI test endpoint is
    wired in through `transport=`."""

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
        # EmbedClient shares this bridge instance with its single httpx.Client
        # (transport=), so httpx.Client.close() calls this once per
        # EmbedClient.close(). Idempotent regardless of whether the wrapped
        # async transport tolerates a second aclose().
        if self._closed:
            return
        self._closed = True
        asyncio.run(self._async_transport.aclose())


class EmbedClient:
    def __init__(self, cfg: EmbedConfig,
                transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        headers = {}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        if transport is not None and not isinstance(transport, httpx.BaseTransport):
            transport = _SyncBridgeTransport(transport)
        self._embed = httpx.Client(base_url=cfg.base_url, headers=headers,
                                   timeout=60.0, transport=transport)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start:start + EMBED_BATCH]
            try:
                # base_url already carries the "/v1" prefix (OpenAI-compatible
                # convention, e.g. "http://host/v1"); a "/v1/..." path here
                # would double it to ".../v1/v1/embeddings".
                response = self._embed.post("/embeddings", json={
                    "model": self.cfg.embed_model, "input": batch})
                response.raise_for_status()
                data = response.json()["data"]
                ordered = sorted(data, key=lambda d: d["index"])
                vectors.extend(d["embedding"] for d in ordered)
            except Exception as exc:
                raise EmbedError(
                    f"embeddings request failed: {_error_detail(exc)}"
                ) from exc
        return vectors

    def close(self) -> None:
        self._embed.close()
