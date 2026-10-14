import { useId } from "react";
import { Link } from "react-router";
import type { MCPServer } from "./mcp";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldDescription, FieldLabel, FieldSet, FieldLegend } from "~/components/ui/field";

export default function MCPSelection({
  servers,
  selected,
  disabled,
  onChange,
}: {
  servers: MCPServer[];
  selected: string[];
  disabled: boolean;
  onChange: (ids: string[]) => void;
}) {
  const prefix = useId();
  return (
    <FieldSet>
      <FieldLegend>MCP servers</FieldLegend>
      <FieldDescription>
        Selected tools are available during agent runs. Add connections and test their tools in{" "}
        <Link to="/mcp-servers" className="underline underline-offset-4">
          MCP servers
        </Link>
        .
      </FieldDescription>
      {servers.length === 0 && (
        <FieldDescription>No MCP servers configured. The agent can still use its built-in tools.</FieldDescription>
      )}
      {servers.map((server) => {
        const ready = server.enabled && !!server.last_tested_at && !server.last_test_error && server.tools.length > 0;
        return (
          <Field key={server.id} orientation="horizontal">
            <Checkbox
              id={`${prefix}-${server.id}`}
              aria-label={`Use MCP ${server.name}`}
              checked={selected.includes(server.id)}
              disabled={disabled || (!ready && !selected.includes(server.id))}
              onCheckedChange={(checked) =>
                onChange(checked === true ? [...selected, server.id] : selected.filter((id) => id !== server.id))
              }
            />
            <div>
              <FieldLabel htmlFor={`${prefix}-${server.id}`}>{server.name}</FieldLabel>
              <FieldDescription>
                {server.description || server.url} ·{" "}
                {ready
                  ? `${server.tools.length} tools`
                  : !server.enabled
                    ? "Disabled"
                    : "Successful connection test required"}
              </FieldDescription>
            </div>
          </Field>
        );
      })}
    </FieldSet>
  );
}
