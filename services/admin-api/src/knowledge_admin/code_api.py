"""Administrator tests for the existing read-only code analysis service."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from docs_wiki.code_client import CodeAgentError
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import load_profile


class CodeQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    repository: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=10000)
    context: str | None = Field(default=None, max_length=4000)


def build_api(code, code_url: str | None):
    router = APIRouter(prefix='/agents/code')

    @router.get('/status')
    def status(agent_id: str = "code"):
        repositories = []
        model = None
        reason = None
        selected = None
        try:
            selected = load_profile(agent_id)
        except ModelUnavailable as exc:
            reason = str(exc)
        if code is None:
            reason = 'The code search service is not configured.'
        else:
            try:
                repositories = [r for r in code.repos() if selected is not None and selected["repositories_enabled"] and (selected["all_repositories"] or r["name"] in selected["repository_ids"])]
                if not any(repo['path_exists'] for repo in repositories):
                    reason = reason or 'Sync a GitHub repository before testing code search.'
            except CodeAgentError:
                reason = 'The code search service is unavailable. Check its connection and repository inventory.'
        return {'repositories': repositories, 'model': None,
            'ask_enabled': selected is not None, 'ask_reason': reason}

    @router.post('/ask')
    async def ask(body: CodeQuestion, agent_id: str = "code"):
        try:
            selected = load_profile(agent_id)
        except ModelUnavailable as exc:
            raise HTTPException(404, str(exc)) from None
        if not selected['repositories_enabled'] or (not selected['all_repositories'] and body.repository not in selected['repository_ids']):
            raise HTTPException(403, 'Repository is outside this agent configuration.')
        return PlainTextResponse('TODO')

    return router
