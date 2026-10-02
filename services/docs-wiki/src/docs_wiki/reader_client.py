"""Client for admin-api's internal Reader conversation endpoints."""
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

UNAVAILABLE = 'Chat is temporarily unavailable.'
PASS_THROUGH = {400, 404, 409}


class ReaderError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class ReaderClient:
    def __init__(self, base_url: str, key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url, self.key, self.transport = base_url, key, transport

    def _client(self, subject: str, email: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, transport=self.transport, timeout=httpx.Timeout(30, read=330),
            headers={'X-Reader-Key': self.key, 'X-Reader-User': subject, 'X-Reader-Email': email})

    @staticmethod
    async def _raise(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        await response.aread()
        if response.status_code in PASS_THROUGH:
            try:
                message = response.json()['error']['message']
            except (ValueError, KeyError, TypeError):
                message = UNAVAILABLE
            raise ReaderError(response.status_code, message)
        raise ReaderError(503, UNAVAILABLE)

    async def run(self, subject: str, email: str, body: dict[str, Any]) -> AsyncIterator[tuple[str, dict]]:
        client = self._client(subject, email)
        try:
            response = await client.send(client.build_request('POST', '/internal/reader/runs', json=body), stream=True)
            await self._raise(response)
        except httpx.HTTPError:
            await client.aclose()
            raise ReaderError(503, UNAVAILABLE) from None
        except ReaderError:
            await client.aclose()
            raise

        async def events():
            name, data = '', ''
            try:
                async for line in response.aiter_lines():
                    if line.startswith('event: '):
                        name = line[7:]
                    elif line.startswith('data: '):
                        data += line[6:]
                    elif line == '' and name:
                        try:
                            yield name, json.loads(data)
                        except ValueError:
                            pass
                        name, data = '', ''
            except httpx.HTTPError:
                return
            finally:
                await response.aclose()
                await client.aclose()
        return events()

    async def _json(self, subject: str, email: str, method: str, path: str) -> Any:
        async with self._client(subject, email) as client:
            try:
                response = await client.request(method, path)
            except httpx.HTTPError:
                raise ReaderError(503, UNAVAILABLE) from None
            await self._raise(response)
            return None if response.status_code == 204 else response.json()

    async def sessions(self, subject: str, email: str) -> list[dict]:
        return (await self._json(subject, email, 'GET', '/internal/reader/sessions'))['sessions']

    async def transcript(self, subject: str, email: str, key: str) -> dict:
        return await self._json(subject, email, 'GET', f'/internal/reader/sessions/{key}')

    async def delete(self, subject: str, email: str, key: str) -> None:
        await self._json(subject, email, 'DELETE', f'/internal/reader/sessions/{key}')
