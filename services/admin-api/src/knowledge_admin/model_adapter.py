"""Connect saved model settings directly to the Agents SDK."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from agents import Model, ModelSettings, OpenAIChatCompletionsModel, OpenAIResponsesModel
from agents.extensions.models.litellm_model import LitellmModel
from knowledge_llm import completion_model
from litellm import model_cost
from openai import AsyncOpenAI
from openai.types.shared import Reasoning


@asynccontextmanager
async def agent_model(model: dict[str, Any], api_key: str | None,
                      timeout_seconds: float) -> AsyncIterator[tuple[Model, ModelSettings]]:
    """Own the provider client for one agent run."""
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
            effort = model.get('reasoning_effort')
            yield sdk_model, ModelSettings(store=False, include_usage=True,
                reasoning=Reasoning(effort=effort) if effort is not None else None)
    else:
        if not api_key:
            raise ValueError('Supply the saved API key for this native model connection.')
        sdk_model = LitellmModel(model=completion_model(model['provider'], model['model']),
            api_key=api_key, base_url=model['base_url'] if model['provider'] != 'bedrock' else None)
        # Resolve the saved model before adding transport prefixes such as bedrock/invoke/.
        # Otherwise LiteLLM's native path can fall back to 4096 despite knowing this model's allowance.
        capabilities = model_cost.get(model['model'], {})
        extra_args = {'timeout': timeout_seconds, 'num_retries': 0, 'drop_params': True}
        if model['provider'] == 'bedrock':
            extra_args['aws_region_name'] = model['aws_region']
        reasoning = None
        extra_body = None
        if 'claude' in model['model'].lower():
            if capabilities.get('supports_prompt_caching'):
                # Cache the stable instructions and the growing conversation prefix per request.
                extra_args['cache_control_injection_points'] = [
                    {'location': 'message', 'role': 'system'},
                    {'location': 'message', 'index': -1},
                ]
            if capabilities.get('supports_reasoning'):
                # The typed SDK setting also preserves Claude's signed thinking across tool calls.
                reasoning = Reasoning(effort='xhigh' if capabilities.get('supports_xhigh_reasoning_effort') else 'high')
                if model['provider'] == 'bedrock' and capabilities.get('supports_adaptive_thinking'):
                    # LiteLLM 1.100 maps Invoke structured outputs through Claude 3,
                    # which incorrectly converts effort into legacy thinking budgets.
                    extra_body = {'thinking': {'type': 'adaptive'}, 'output_config': {'effort': reasoning.effort}}
        yield sdk_model, ModelSettings(max_tokens=capabilities.get('max_output_tokens'),
            reasoning=reasoning, include_usage=True, extra_args=extra_args, extra_body=extra_body)
