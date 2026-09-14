# Agent function tools

Import decorated functions directly. There is no tool factory or registry.

- `files.py`: `list_files`, `read_file`, `search_files`. Read-only access to the
  enabled code/document roots and the run's workspace. Absolute paths are checked
  after resolving symlinks; searches use the installed `rg` executable.
- `ssh.py`: `ssh_run`. Resolves a selected server and its credential reference from
  the supplied snapshot/context. Reuses `knowledge_admin.ssh.execute`, including
  optional host verification, sudo restrictions, timeout and cancellation handling.
- `shell.py`: `shell_run`. Runs `/bin/sh` in the supplied workspace, with a clean
  environment, bounded output, timeout and process-group cleanup. It requires an
  explicit `shell_enabled=True` in trusted context **and an isolated worker**.
  Neither `cwd` nor environment filtering restrict filesystem/network access.
  This function must not be enabled in the administration process.

`AgentContext` is just a TypedDict documenting the existing configuration snapshot,
resolved credentials, workspace and shell capability. The SDK injects it; none of
these values appear in the model-facing tool argument schema. Tools do not query
PostgreSQL or decrypt keys. Include the selected resource descriptions, server
instructions and capability rules in the agent's instructions when assembling it.

```python
from agents import Agent, RunConfig, Runner
from knowledge_admin.tools.files import list_files, read_file, search_files
from knowledge_admin.tools.ssh import ssh_run

# config, credentials, workspace, model and instructions are supplied by the caller.
selected_tools = []
if config['code']['enabled'] or config['documents']['enabled']:
    selected_tools.extend([list_files, read_file, search_files])
if config['ssh']['enabled']:
    selected_tools.append(ssh_run)

assistant = Agent(name=config['name'], model=model,
                  instructions=instructions, tools=selected_tools)
run = Runner.run_streamed(assistant, task, context={
    'config': config, 'credentials': credentials,
    'workspace': workspace, 'shell_enabled': False,
}, run_config=RunConfig(tracing_disabled=True))
```

The specialist runner in `knowledge_admin.agents.agent` registers these functions
directly. The shell tool stays disabled in the administration process. MCP uses
SDK integration; skills and Mermaid guidance remain instructions.

The SDK's `FileSearchTool` searches OpenAI-hosted vector stores, not these local
folders or our PostgreSQL index. `WebSearchTool` is also an OpenAI-hosted Responses
tool. Neither is currently registered. `ShellTool` requires a local executor or
an OpenAI-hosted environment. The installed LiteLLM/Bedrock integration accepts
function tools, so those SDK tool classes cannot be substituted directly for our
functions. `search_files` is local literal-text search; a tool for our RAG
`/search` API would be a separate capability.
