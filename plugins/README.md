# Preserved app-docs plugin

Copied unchanged from:

- Source: `/Users/graovic/fincore/knowledge-agent-v2/plugins/app-docs`
- Source commit: `2f0507fd52774b3cc163907962761c410e7c5874`
- Plugin version: `0.3.2`
- Copied: 2026-09-08

The plugin is available locally without installing it into the operator's Claude
configuration. The future worker will pass its absolute directory through the
Claude Agent SDK's `plugins` option and invoke `/app-docs:docs` explicitly.

The preserved plugin still has the v2 interactive, clean-Git-worktree workflow.
Before connecting the admin Generate button, implement the admin invocation
contract described in [architecture](../docs/architecture.md). The vendored
plugin has not yet been modified to implement that contract; do not advertise
unattended generation as already working.
