import hashlib

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

EMBED_DIM = 8


def fake_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [b / 255 for b in digest[:dim]]


@pytest.fixture()
def fake_embed():
    state = {
        "embed_batches": [],
        "fail_embed": False,
        "truncate_embeddings": False,
        # When set to a string, the endpoint answers 400 with an
        # OpenAI-style {"error": {"message": ...}} body carrying it.
        "embed_error_body": None,
        # Override the embedding dimension returned by /v1/embeddings, to
        # simulate a backend/model swap that keeps the configured model
        # *name* the same but changes vector shape underneath (see
        # test_dimension_drift_invalidates). None means "use EMBED_DIM".
        "embed_dim": None,
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

    return httpx.ASGITransport(app=api), state
