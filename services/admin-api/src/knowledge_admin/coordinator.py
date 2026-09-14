"""Agents-as-tools orchestration followed by a final summary, using native SDK tracing."""

import asyncio
from typing import Any

from agents import Agent, FunctionTool, Model, ModelSettings, RunConfig, Runner, RunResultStreaming, trace

from knowledge_admin.agent_activity import ActivityCallback, SdkActivity, redact
from knowledge_admin.agent_tracing import trace_secrets


async def coordinator(*, model: Model, model_settings: ModelSettings, system_prompt: str,
                      summary_prompt: str, task: str | list[dict[str, Any]], tools: list[FunctionTool],
                      on_activity: ActivityCallback, secrets: list[str], max_turns: int,
                      max_subtasks: int, timeout_seconds: int) -> RunResultStreaming:
    """Run selected specialist tools, then pass their returned evidence to a summarizer."""
    if not system_prompt.strip() or not summary_prompt.strip() or not task or not tools:
        raise ValueError('Provide both prompts, a task, and at least one subagent tool.')
    if min(max_turns, max_subtasks, timeout_seconds) < 1:
        raise ValueError('Coordinator limits must be positive.')
    if len({tool.name for tool in tools}) != len(tools):
        raise ValueError('Each subagent must have a unique tool name.')
    instructions = system_prompt + '\n\nDelegate using the exact tool names provided for this run. '
    instructions += 'Provide self-contained assignments with relevant context and earlier findings. '
    instructions += f'You can create at most {max_subtasks} subtasks. Preserve evidence and failures for the final summary.'
    orchestrator = Agent(name='Coordinator', instructions=instructions, model=model,
                         model_settings=model_settings, tools=tools)
    summarizer = Agent(name='Summary', instructions=summary_prompt, model=model, model_settings=model_settings)
    activity = SdkActivity(secrets, on_activity)
    with trace_secrets(secrets), trace('Agent board task'):
        async with asyncio.timeout(timeout_seconds):
            conversation = task
            usage = None
            for assistant in (orchestrator, summarizer):
                run = Runner.run_streamed(assistant, conversation, hooks=activity,
                    max_turns=max_turns if assistant is orchestrator else 1,
                    run_config=RunConfig(tracing={'include_task_and_turn_spans': False}))
                try:
                    async for event in run.stream_events():
                        await activity.notify(event)
                finally:
                    run.cancel()
                    try:
                        async for _ in run.stream_events():
                            pass
                    except (Exception, asyncio.CancelledError):
                        pass
                if assistant is orchestrator:
                    # Bedrock requires a user message after the orchestrator's final answer.
                    conversation = [*run.to_input_list(), {'role': 'user', 'content':
                        'Write the final response to the original task using the conversation and specialist results above.'}]
                if usage is None:
                    usage = run.context_wrapper.usage
                else:
                    usage.add(run.context_wrapper.usage)
            run.context_wrapper.usage = usage
            run.final_output = redact(str(run.final_output), secrets)
            return run
