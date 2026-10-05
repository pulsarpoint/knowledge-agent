import hashlib
import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from docs_wiki.workspace_client import RagError, RagNotFound

EMBED_DIM = 8


def fake_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [b / 255 for b in digest[:dim]]


@pytest.fixture()
def fake_llm():
    state = {
        "embed_batches": [],
        "chat_requests": [],
        "reply": "The refund flow publishes an event [1].",
        "fail_embed": False,
        "fail_chat": False,
        "truncate_embeddings": False,
        # When set to a string, the endpoint answers 400 with an
        # OpenAI-style {"error": {"message": ...}} body carrying it.
        "chat_error_body": None,
        "embed_error_body": None,
        # Simulate OpenAI reasoning-family models: 400 any chat request
        # that carries an explicit "temperature" parameter.
        "reject_temperature": False,
        # Override the embedding dimension returned by /v1/embeddings, to
        # simulate a backend/model swap that keeps the configured model
        # *name* the same but changes vector shape underneath (see
        # test_dimension_drift_invalidates). None means "use EMBED_DIM".
        "embed_dim": None,
        # Scripted STREAMING chat turns (Agents SDK path). Each streaming
        # request pops one item; see test_llm._openai_stream for shapes.
        "chat_script": [],
    }
    api = FastAPI()

    @api.post("/v1/embeddings")
    async def embeddings(request: Request):
        if state["fail_embed"]:
            raise RuntimeError("embed endpoint down")
        payload = await request.json()
        inputs = payload["input"]
        if isinstance(inputs, str):
            inputs = [inputs]
        state["embed_batches"].append(list(inputs))
        if state["embed_error_body"]:
            return JSONResponse(status_code=400, content={
                "error": {"message": state["embed_error_body"]}})
        if state["truncate_embeddings"]:
            # Simulate a malformed-but-HTTP-200 response: an empty `data`
            # array despite the request having `input`s.
            return {"data": []}
        dim = state["embed_dim"] or EMBED_DIM
        return {"data": [{"index": i, "embedding": fake_embedding(t, dim)}
                         for i, t in enumerate(inputs)]}

    @api.post("/v1/chat/completions")
    async def chat(request: Request):
        if state["fail_chat"]:
            raise RuntimeError("chat endpoint down")
        payload = await request.json()
        state["chat_requests"].append(payload)
        if state["chat_error_body"]:
            return JSONResponse(status_code=400, content={
                "error": {"message": state["chat_error_body"]}})
        if state["reject_temperature"] and "temperature" in payload:
            return JSONResponse(status_code=400, content={
                "error": {"message": "Unsupported value: 'temperature' does "
                                     "not support 0 with this model. Only "
                                     "the default (1) value is supported.",
                          "type": "invalid_request_error",
                          "param": "temperature",
                          "code": "unsupported_value"}})
        if not payload.get("stream"):
            return {"choices": [{"message": {"role": "assistant",
                                              "content": state["reply"]}}]}

        step = (state["chat_script"].pop(0) if state["chat_script"]
                else {"text": state["reply"]})
        base = {"id": "chatcmpl-fake", "object": "chat.completion.chunk",
                "created": 0, "model": payload.get("model", "fake")}

        def sse(obj):
            return f"data: {json.dumps(obj)}\n\n"

        async def stream():
            if "tool" in step:
                call = {"index": 0,
                        "id": f"call_{len(state['chat_requests'])}",
                        "type": "function",
                        "function": {
                            "name": step["tool"]["name"],
                            "arguments": json.dumps(step["tool"]["arguments"])}}
                yield sse({**base, "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "tool_calls": [call]},
                    "finish_reason": None}]})
                yield sse({**base, "choices": [{
                    "index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
            elif step.get("error"):
                yield sse({**base, "choices": [{
                    "index": 0, "delta": {"role": "assistant", "content": ""},
                    "finish_reason": None}]})
                raise RuntimeError("scripted mid-stream failure")
            else:
                text = step["text"]
                half = max(1, len(text) // 2)
                yield sse({**base, "choices": [{
                    "index": 0, "delta": {"role": "assistant",
                                           "content": text[:half]},
                    "finish_reason": None}]})
                if text[half:]:
                    yield sse({**base, "choices": [{
                        "index": 0, "delta": {"content": text[half:]},
                        "finish_reason": None}]})
                yield sse({**base, "choices": [{
                    "index": 0, "delta": {}, "finish_reason": "stop"}]})
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    return httpx.ASGITransport(app=api), state


class StubRag:
    """In-memory stand-in for `WorkspaceRagClient`, driven by a mutable
    `state` dict, raising the same `RagError`/`RagNotFound` the client does."""

    def __init__(self, state):
        self.state = state

    def _check(self):
        if self.state["fail"]:
            raise RagError("request failed: rag backend failure")

    def collections(self):
        self._check()
        return self.state["collections"]

    def list_documents(self, collection):
        self._check()
        if collection not in self.state["documents"]:
            raise RagError(f"request failed: unknown collection: {collection}")
        return self.state["documents"][collection]

    def get_document(self, collection, path):
        self._check()
        doc = self.state["contents"].get((collection, path))
        if doc is None:
            raise RagNotFound(f"document not found: {collection}/{path}")
        return doc

    def search(self, collection, query, top=8):
        self._check()
        self.state["search_requests"].append(
            {"collection": collection, "query": query, "top": top})
        if (collection not in self.state["documents"]
                and collection not in self.state["search_results"]):
            raise RagError(f"request failed: unknown collection: {collection}")
        return self.state["search_results"].get(collection, [])


@pytest.fixture()
def fake_rag():
    state = {
        # collection -> [catalog dicts]
        "documents": {},
        # (collection, path) -> full-document dict
        "contents": {},
        # collection -> [hit dicts]
        "search_results": {},
        # True -> every call raises RagError.
        "fail": False,
        # Recorded {"collection": ..., "query": ..., "top": ...} per search.
        "search_requests": [],
        "collections": [],
    }
    return StubRag(state), state
