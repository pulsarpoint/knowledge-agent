"""The subagent tool: run_subagent hands a separate part of a native agent's work to a helper that runs in parallel;
the runtime is knowledge_agents/subagents.py."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.tools.registry import tool


class SubagentResources(BaseModel):
    model_config = ConfigDict(extra='forbid')

    code_project_ids: list[str] = Field(
        default_factory=list, description='Code project IDs from get_available_resources.'
    )
    collection_ids: list[str] = Field(
        default_factory=list, description='Document collection IDs from get_available_resources.'
    )
    server_ids: list[str] = Field(default_factory=list, description='SSH server IDs from get_available_resources.')
    service_ids: list[str] = Field(default_factory=list, description='Service IDs from get_available_resources.')


@tool
async def run_subagent(
    context: RunContext,
    task: Annotated[
        str, Field(description='The complete task, up to 20000 characters: the helper does not see this conversation.')
    ],
    tools: Annotated[
        list[str], Field(description='Names of your tools the helper may use, as you see them; at least one.')
    ],
    resources: Annotated[
        SubagentResources | None,
        Field(description='The resources the helper may use, from yours; omit it to give all of yours.'),
    ] = None,
) -> str:
    """Start a helper for a separate, well-defined part of your work, such as exploring one project or checking one question. Give it a complete task, because it does not see this conversation. Give it the fewest tools and resources it needs. Helpers may run in parallel. You get their final answer as the result; verify important claims."""
    # Imported here: the subagent runtime imports tools.catalog (which imports this module) and the native session runtime.
    from knowledge_admin.knowledge_agents import subagents

    return await subagents.run_subagent(context, task, tools, resources)
