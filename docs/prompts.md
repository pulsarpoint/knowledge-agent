# Agent system prompts

Each agent has one system prompt. Edit it on the agent's **General** tab (**Agents → select the agent → General →
System prompt**), then **Save prompt**. It is required, nonempty and at most 100,000 characters. The new prompt applies
to new conversations; existing conversations keep the configuration they started with. Saves use the agent's revision, so
a stale page returns “Agent changed. Reload before saving.” (409). Prompt text is never copied into audit metadata.

History is not kept. To try a different prompt, **Clone agent**, change the prompt on the clone, test it, and then
**Apply to <original>** (see [Agent execution](agents.md#clones)).

Prompts are plain text instructions. No network access, source-code execution or code execution is performed on saved
prompts. Source repositories and documents remain separate resources. Search diagrams are optional; when a prompt asks
for Mermaid, the diagram is checked before rendering.

`PUT /api/agents/{id}/prompt` saves `{system_prompt, revision}`. The former `/api/llm/prompts` endpoints and the
`ka.agent_prompts` / `ka.agent_prompt_versions` tables were removed by migrations `0079_agent_system_prompt` and
`0080_clone_agents`; the prompt is `ka.agents.system_prompt`.

**Administration → Prompts** (`/prompts`, formerly Docs → Prompts) is the tagged prompt library, not an agent's prompt; see
[documentation](documentation.md).
