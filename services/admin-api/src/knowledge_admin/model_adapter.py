"""Connect saved model settings directly to the Agents SDK."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from agents import Model, ModelSettings, OpenAIChatCompletionsModel, OpenAIResponsesModel
from agents.extensions.models.litellm_model import LitellmModel
from knowledge_llm import completion_model
from openai import AsyncOpenAI


@asynccontextmanager
async def agent_model(model: dict[str, Any], api_key: str | None,
                      timeout_seconds: float) -> AsyncIterator[tuple[Model, ModelSettings]]:
    """Own the provider client for one coordinator or specialist run."""
    if (model['provider'], model['api_format']) not in (
        ('openai', 'responses'), ('openai', 'chat_completions'),
        ('anthropic', 'native'), ('bedrock', 'native'),
    ):
        raise ValueError('Unsupported model provider or API format.')
    if model['provider'] == 'openai':
        async with AsyncOpenAI(api_key=api_key or 'unused', base_url=model['base_url'],
                               max_retries=0, timeout=timeout_seconds) as client:
            sdk_model = (OpenAIResponsesModel if model['api_format'] == 'responses' else OpenAIChatCompletionsModel)(
                model=model['model'], openai_client=client)
            yield sdk_model, ModelSettings(store=False, include_usage=True)
    else:
        if not api_key:
            raise ValueError('Supply the saved API key for this native model connection.')
        sdk_model = LitellmModel(model=completion_model(model['provider'], model['model']),
            api_key=api_key, base_url=model['base_url'] if model['provider'] != 'bedrock' else None)
        yield sdk_model, ModelSettings(include_usage=True, extra_args={'timeout': timeout_seconds, 'num_retries': 0,
            'drop_params': True, **({'aws_region_name': model['aws_region']} if model['provider'] == 'bedrock' else {})})
