import hashlib
import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

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


@pytest.fixture()
def fake_rag():
    state = {
        # collection -> [catalog dicts], per GET .../documents
        "documents": {},
        # (collection, path) -> full-document dict, per GET .../documents/{path}
        "contents": {},
        # collection -> [hit dicts], per POST .../search
        "search_results": {},
        # True -> every route below answers 500 with the error shape,
        # regardless of what "documents"/"contents"/"search_results" hold.
        "fail": False,
        # Recorded {"collection": ..., "query": ..., "top": ...} per search.
        "search_requests": [],
        # GET /collections rows, mirroring the real service's shape
        # exactly (services/rag/src/rag/api.py list_collections):
        # {"name", "indexed", "documents", "retrieval", "kind"}. Empty by
        # default — tests that need code-collection discovery seed this
        # explicitly with a row carrying "kind": "code".
        "collections": [],
    }
    api = FastAPI()

    def _error(status_code: int, message: str) -> JSONResponse:
        return JSONResponse(status_code=status_code,
                            content={"error": {"message": message}})

    @api.get("/collections")
    async def collections():
        if state["fail"]:
            return _error(500, "rag backend failure")
        return state["collections"]

    @api.get("/collections/{name}/documents")
    async def documents(name: str):
        if state["fail"]:
            return _error(500, "rag backend failure")
        if name not in state["documents"]:
            return _error(404, f"unknown collection: {name}")
        return state["documents"][name]

    @api.get("/collections/{name}/documents/{path:path}")
    async def get_document(name: str, path: str):
        if state["fail"]:
            return _error(500, "rag backend failure")
        doc = state["contents"].get((name, path))
        if doc is None:
            return _error(404, f"document not found: {name}/{path}")
        return doc

    @api.post("/collections/{name}/search")
    async def search(name: str, request: Request):
        if state["fail"]:
            return _error(500, "rag backend failure")
        payload = await request.json()
        state["search_requests"].append({"collection": name, **payload})
        if name not in state["documents"] and name not in state["search_results"]:
            return _error(404, f"unknown collection: {name}")
        return state["search_results"].get(name, [])

    return httpx.ASGITransport(app=api), state
