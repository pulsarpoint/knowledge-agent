# Claude Code agents and interactive generation — design

Date: 2026-10-07. Status: draft for review.

## Goal

Add a second agent runtime, the **Claude Agent SDK** (Claude Code), next to the existing
OpenAI Agents SDK agents. A Claude Code agent is generic: it receives a prompt, the
folders it may work in, and any other context (server addresses, notes). It does not know
whether it is documenting a project, a service or anything else.

Documentation generation with a Claude Code agent is **interactive**:

- the run streams to the page as it happens;
- you can send messages to the agent while it works;
- you review the draft and save it as a document.

## Claude Code agents

- **All agents** lists them with a **Claude Code SDK** badge. The existing agents are
  OpenAI SDK agents and are unchanged.
- **New agent → Claude Code agent** asks for:

  | Field | Notes |
  | --- | --- |
  | Name | Required |
  | Description | Optional |
  | Model | Claude models only: Bedrock (`eu.anthropic.claude-…` inference profiles) or Anthropic API connections from LLM Settings |
  | Instructions | Always-on system instructions, appended to Claude Code's own |

- **Tools:** read-only file tools (Read, Grep, Glob, LS) plus **Bash**. They are fixed for
  this runtime.
  - Claude Code runs in its own container, where `DATA_ROOT` is mounted **read-only**. It
    can read and run read-only commands, but it cannot change the data folders.
  - The container has network access, which it needs to reach Bedrock or Anthropic.
    Bash can therefore reach the network too. The admin accepted this.
  - Edit and Write tools, MCP and sub-agents are off.
- **No limits:** no maximum turns, time or cost. A run continues until the agent finishes
  or the user presses **Stop**.
- **Storage:**
  - `ka.agents` gains `runtime` (`openai` | `claude_code`, default `openai`).
  - Claude Code agents use `model_id` and their instructions, kept in the existing
    versioned config.
  - The OpenAI-only capability switches (documents, repositories, SSH, skills, MCP,
    services, coordinator) are always off for them.
  - They never appear in places that run OpenAI agents: the Reader, the multi-agent board
    and test chats.

## Generating documentation with an agent

**Docs → Generate documents → New → With an agent**, alongside **Service**, **Whole
environment** and **Code project**:

| Field | Notes |
| --- | --- |
| Agent | A Claude Code agent |
| Model | The agent's model by default; any Claude connection can be chosen for this run |
| Prompt | Any active documentation prompt |
| Context | Any mix of: **code projects** (their folders become working folders), **folders** under `DATA_ROOT` (picked with the folder browser), **servers** (name, address and description passed as text) and **notes** (free text) |
| Comments | Extra instructions for this run |
| Document name and folder | As for code project documents: lowercase folder, default `<first-project>-docs` or `docs`; same-name confirmation on save |

- **Agent input:** the agent gets one message containing the prompt, the comments and a
  context section. The context lists the working folders as absolute container paths,
  the servers and the notes.
- **Working directory:** Claude Code's working directory is the first folder. Every
  folder is added as an allowed directory.
- **Servers are context only in this step.** No SSH credentials are given. Read-only
  server access is a later step.
- **Other generation modes are unchanged:** the existing non-interactive **Service**,
  **Whole environment** and **Code project** generation stays, using OpenAI agents.

## Interactive session

- **Generate** creates a session and opens **Docs → Sessions → <session>**. The worker
  starts the agent right away.
- **The session page** streams events over Server-Sent Events from admin-api:
  - assistant messages and thinking summaries;
  - each tool call and its short result: file read (path), search (pattern), Bash
    (command and the first lines of output);
  - running token usage (in, out, cached) and status: starting, working, waiting for you,
    finished, stopped or failed.
- **Send message** delivers your text to the agent:
  - while it is working, the message is queued and reaches the agent at its next step;
  - after it finished, the message starts a new turn in the same conversation, for
    example "shorten the API section".
- **Stop** interrupts the current turn. The session can continue with a new message.
- **Draft:** each finished turn's final answer becomes the **draft**, shown as rendered
  Markdown next to the stream.
- **Save document** saves the current draft as a versioned document. It is linked to the
  session's code projects, stored in its folder and exported to `<folder>/<slug>/`, with
  the same name rules and confirmation as code project documents. Nothing is saved
  automatically.
- **Leaving the page does not stop the session.** Sessions are listed under **Docs →
  Sessions** with status, agent, tokens and document. Reopening shows the full history.
- **Close session** ends it and releases the worker. Sessions idle for 24 hours are closed
  automatically, and the history is kept.
- **Run records:** every event is also stored as an agent run record, so the session
  appears in **Activity → Agent runs**.

## Runtime

- **New service `claude-code-worker`:**
  - Python, with `claude-agent-sdk` and its bundled Claude Code CLI;
  - mounts `DATA_ROOT` read-only;
  - has network access and no other writable mounts except its own scratch home
    directory.
- **Sessions:** the worker claims sessions from PostgreSQL, one `ClaudeSDKClient` per
  active session.
  - It appends events to `ka.agent_run_events`.
  - It reads queued user messages from `ka.agent_session_inputs`.
  - It answers stop requests with `interrupt()`.
- **Credentials:** the model connection's key is decrypted only in the worker and passed
  to the CLI process environment.
  - Bedrock: `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_REGION` and `AWS_BEARER_TOKEN_BEDROCK`.
  - Anthropic: `ANTHROPIC_API_KEY`.
  - The key is never written to disk or logged.
- **New tables:**
  - `ka.agent_sessions_cc` holds id, project, agent, model, prompt snapshot, context
    snapshot, status, draft, document id and timestamps.
  - `ka.agent_session_inputs` holds queued user messages with sequence and delivered
    time.
- **admin-api routes:** create session, stream events (SSE), send message, stop, close,
  save document, list sessions.

## Errors

| Problem | Behaviour |
| --- | --- |
| Model is not a Claude connection | 400 when saving the agent or starting a session |
| Model connection fails (for example Bedrock needs an inference profile) | Session status failed, with the provider's reason made safe (no credentials) and a hint |
| Worker not running | The session stays "starting" with a notice; it begins when the worker is up |
| Same document name in the folder on save | Confirm to save a new version |
| A folder that no longer exists | Refused when starting |

## Testing

- **admin-api:**
  - agent runtime validation (Claude models only);
  - Claude Code agents are hidden from OpenAI-only places;
  - session create, send, stop and close;
  - the SSE stream with a fake worker;
  - save document;
  - context snapshot (projects to folders, servers, notes).
- **Worker:**
  - a fake `ClaudeSDKClient` drives events, queued input, interrupt and draft capture;
  - the environment carries the credentials but no key appears in events or logs;
  - read-only mount enforcement is checked by compose rendering.
- **admin-ui:**
  - Claude Code agent create and edit with the badge;
  - the With an agent form;
  - the session page stream, send, stop and save;
  - the Sessions list.
- **Compose:** the `claude-code-worker` mounts and environment.

## Out of scope

- SSH or other server access for Claude Code agents.
- Using Claude Code agents in the Reader, the board or test chats.
- Limits on turns, time or cost.
