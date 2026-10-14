"""Bind catalog tools and experts to the OpenAI Agents SDK. The only module that knows how a tool reaches the model;
another framework would replace this file, not the catalog."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agents import FunctionTool
from agents.tracing import SpanError, get_current_span
from agents.strict_schema import ensure_strict_json_schema
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.sessions.activity import redact
from knowledge_admin.capabilities.context import TOOL_CALL_ID
from knowledge_admin.tools.catalog import ToolSpec

logger = logging.getLogger(__name__)
NOT_OFFERED = 'Tool {name} is not available in this run.'


ENUM_LIMIT = 50  # above this, the schema lists no choices; the tool's own check still applies


def function_tool(spec: ToolSpec, usage: str, choices: list[dict[str, Any]] | None = None) -> FunctionTool:
    """One catalog tool as an SDK function tool: the usage text is its description (see _function_tool). choices: the
    run's document_access entries; an edit tool's document_id then lists only the editable ones (up to ENUM_LIMIT)."""
    schema = spec.arguments.model_json_schema()
    editable = [entry for entry in choices or [] if entry.get('editable')]
    if editable and len(editable) <= ENUM_LIMIT and 'document_id' in schema.get('properties', {}):
        schema['properties']['document_id']['enum'] = [str(entry['id']) for entry in editable]
        usage = (
            usage
            + '\n\nDocuments you may change: '
            + ', '.join(f'{entry["title"]} ({entry["id"]})' for entry in editable)
        )
    return _function_tool(spec.name, usage, spec.arguments, spec.run, schema)


class ExpertResources(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code_project_ids: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)
    server_ids: list[str] = Field(default_factory=list)
    service_ids: list[str] = Field(default_factory=list)


class ExpertTask(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task: str = Field(description='A complete task: the expert does not see this conversation.')


class PerRunExpertTask(ExpertTask):
    resources: ExpertResources | None = Field(description='The resources the expert works on; only your own.')


def expert_tool(entry: dict[str, Any]) -> FunctionTool:
    """One expert as its own tool (ask_<name>): the call goes to the turn's Subagents host (Subagents.ask)."""
    arguments = PerRunExpertTask if entry['resources'] == 'per_run' else ExpertTask

    async def run(context: dict[str, Any], value: BaseModel) -> str:
        host = context.get('subagents')
        if host is None:
            raise ValueError(NOT_OFFERED.format(name=entry['tool']))
        resources = getattr(value, 'resources', None)
        return await host.ask(
            context, entry, value.task, resources.model_dump() if resources else None, TOOL_CALL_ID.get() or ''
        )

    return _function_tool(entry['tool'], entry['description'], arguments, run)


def _function_tool(
    name: str,
    description: str,
    arguments: type[BaseModel],
    run: Callable[[Any, BaseModel], Awaitable[str]],
    schema: dict[str, Any] | None = None,
) -> FunctionTool:
    """One SDK function tool: `description` is what the model reads and the argument model its strict JSON schema.

    Invocation refuses a tool missing from context['tools'] (the run's offered tools, a backstop), validates the
    arguments and runs `run`. Errors are logged and set on the current trace span with the run's
    credentials redacted, as decorated function tools did, and go back to the model in the SDK's default error text,
    also redacted; cancellation propagates."""

    async def invoke(ctx: ToolContext[Any], raw: str) -> str:
        token = TOOL_CALL_ID.set(ctx.tool_call_id)  # run_subagent and experts record their runs under this call
        try:
            if name not in ctx.context.get('tools', ()):
                raise ValueError(NOT_OFFERED.format(name=name))
            value = arguments.model_validate_json(raw or '{}')
            return await run(ctx.context, value)
        except Exception as exc:
            error = redact(str(exc), ctx.context.get('credentials', {}).values())
            span = get_current_span()
            if span is not None:
                span.set_error(
                    SpanError(message='Error running tool (non-fatal)', data={'tool_name': name, 'error': error})
                )
            logger.error('Tool %s failed: %s', name, error)
            return f'An error occurred while running the tool. Please try again. Error: {error}'
        finally:
            TOOL_CALL_ID.reset(token)

    return FunctionTool(
        name=name,
        description=description,
        params_json_schema=ensure_strict_json_schema(schema if schema is not None else arguments.model_json_schema()),
        on_invoke_tool=invoke,
        strict_json_schema=True,
    )
