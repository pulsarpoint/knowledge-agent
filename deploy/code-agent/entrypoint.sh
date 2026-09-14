#!/bin/sh
# CODE_ROOT uses the database inventory; standalone mode mounts one repository.
set -eu
config_path=/tmp/code-agent.toml
if [ -n "${CODE_ROOT:-}" ]; then
    : > "$config_path"
else
    printf '[[repos]]\nname = "%s"\npath = "/work/app"\n' "${CODE_AGENT_REPO_NAME:-app}" > "$config_path"
fi
exec uv run --frozen --no-dev code-agent serve "$config_path" --host 0.0.0.0 --port 8795
