"""The subagent tool: run_subagent hands a separate part of a native agent's work to a helper that runs in parallel;
the runtime is knowledge_agents/subagents.py."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.sessions.run_access import picks
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


def check_run_subagent(access: dict, resources: dict | None = None, **_) -> str | None:
    """None when allowed, else the refusal text: every resource given to the helper is in the run's list (None: all
    of the run's own). The helper's access is the run's, narrowed to them (subagents.subagent_config)."""
    for pick in picks(resources):
        if not access_rules.can_use(access, pick['type'], pick['id']):
            return access_rules.refuse_use(pick['type'])
    return None


@tool
async def run_subagent(
    context: RunContext,
    task: Annotated[
        str, Field(description='The complete task, up to 20000 characters: the helper does not see this conversation.')
    ],
    resources: Annotated[
        SubagentResources | None,
        Field(description='The resources the helper may use, from yours; omit it to give all of yours.'),
    ] = None,
) -> str:
    """Start a helper for a separate, well-defined part of your work, such as exploring one project or checking one question. Give it a complete task, because it does not see this conversation. It gets your tools except run_subagent and the experts; give it the fewest resources it needs. Helpers may run in parallel. You get their final answer as the result; verify important claims."""
    # Imported here: the subagent runtime imports tools.catalog (which imports this module) and the native session runtime.
    from knowledge_admin.knowledge_agents import subagents

    if refusal := check_run_subagent(context['config']['access'], resources=resources):
        return refusal
    return await subagents.run_subagent(context, task, resources)
