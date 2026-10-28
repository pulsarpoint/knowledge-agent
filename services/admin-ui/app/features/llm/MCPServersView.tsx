import { useEffect, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Pencil, Plug, Plus, Unplug } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "~/lib/api";
import { getMCPServers, saveMCPServer, testMCPServer, deleteMCPServer, type MCPServer } from "./mcp";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldGroup, FieldLabel, FieldDescription } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import { Switch } from "~/components/ui/switch";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import ConfirmRemove from "~/components/ConfirmRemove";
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import LeaveGuard, { useSheetGuard } from "~/components/LeaveGuard";

const failed = (e: unknown) => (e instanceof ApiError ? e.message : "MCP operation failed. Try refreshing.");

/** New MCP server / Edit MCP server in a right Sheet: Create MCP server (or Save) and Cancel (which discards); Escape, the X, a click outside or leaving the
 * page with unsaved changes asks Stay / Leave. */
function MCPServerSheet({
  server,
  onClose,
  onSaved,
}: {
  server: MCPServer | null;
  onClose: () => void;
  onSaved: (name: string) => void;
}) {
  const [editing, setEditing] = useState({
    name: server?.name ?? "",
    description: server?.description ?? "",
    url: server?.url ?? "",
    enabled: server?.enabled ?? true,
    token: "",
    removeToken: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty =
    !busy &&
    (editing.name !== (server?.name ?? "") ||
      editing.description !== (server?.description ?? "") ||
      editing.url !== (server?.url ?? "") ||
      editing.enabled !== (server?.enabled ?? true) ||
      editing.token !== "" ||
      editing.removeToken);
  const guard = useSheetGuard(dirty);
  const close = () => guard.close(onClose);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      await saveMCPServer(server?.id ?? null, {
        name: editing.name,
        description: editing.description,
        url: editing.url,
        enabled: editing.enabled,
        ...(server ? { revision: server.revision } : {}),
        ...(editing.removeToken ? { token: null } : editing.token ? { token: editing.token } : {}),
      });
      onSaved(editing.name);
    } catch (e) {
      setError(failed(e));
      setBusy(false);
    }
  }
  return (
    <Sheet open onOpenChange={(next) => !next && close()}>
      <LeaveGuard dirty={dirty} />
      {guard.dialog}
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{server ? "Edit MCP server" : "New MCP server"}</SheetTitle>
          <SheetDescription>
            Streamable HTTP with an optional bearer token. Save, test the connection, then add it on an agent's
            Resources tab.
          </SheetDescription>
        </SheetHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="mcp-name">Name</FieldLabel>
              <Input
                id="mcp-name"
                required
                maxLength={100}
                disabled={busy}
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="mcp-description">Description</FieldLabel>
              <Textarea
                id="mcp-description"
                maxLength={4000}
                disabled={busy}
                value={editing.description}
                onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                placeholder="What these tools provide and when the agent should use them"
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="mcp-url">HTTP endpoint</FieldLabel>
              <Input
                id="mcp-url"
                type="url"
                required
                maxLength={2000}
                disabled={busy}
                value={editing.url}
                onChange={(e) => setEditing({ ...editing, url: e.target.value })}
                placeholder="https://mcp.example.com/mcp"
              />
              <FieldDescription>
                Use an address reachable from Docker. For a service on this machine, use host.docker.internal instead of
                localhost.
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="mcp-token">
                {server?.has_token ? "Replace bearer token" : "Bearer token (optional)"}
              </FieldLabel>
              <Input
                id="mcp-token"
                type="password"
                autoComplete="new-password"
                maxLength={16000}
                disabled={busy || editing.removeToken}
                value={editing.token}
                onChange={(e) => setEditing({ ...editing, token: e.target.value })}
              />
              <FieldDescription>
                {server?.has_token
                  ? "Token is saved encrypted. Leave blank to keep it. Changing the endpoint requires replacing or removing the token."
                  : "Stored encrypted. Leave blank for an endpoint that does not require authentication."}
              </FieldDescription>
            </Field>
            {server?.has_token && (
              <Field orientation="horizontal">
                <Checkbox
                  id="mcp-remove-token"
                  checked={editing.removeToken}
                  disabled={busy}
                  onCheckedChange={(checked) => setEditing({ ...editing, removeToken: checked === true, token: "" })}
                />
                <FieldLabel htmlFor="mcp-remove-token">Remove saved token</FieldLabel>
              </Field>
            )}
            <Field orientation="horizontal">
              <Switch
                id="mcp-enabled"
                checked={editing.enabled}
                disabled={busy}
                onCheckedChange={(enabled) => setEditing({ ...editing, enabled })}
              />
              <FieldLabel htmlFor="mcp-enabled">Enabled</FieldLabel>
            </Field>
          </FieldGroup>
          {error && (
            <Alert variant="destructive">
              <AlertDescription role="alert">{error}</AlertDescription>
            </Alert>
          )}
          <SheetFooter className="gap-2 sm:justify-start sm:space-x-0">
            <Button disabled={busy}>
              {busy && <Spinner data-icon="inline-start" />}
              {server ? "Save" : "Create MCP server"}
            </Button>
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              Cancel
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

type MCPSort = "name" | "enabled";
const byName = (a: MCPServer, b: MCPServer) => a.name.localeCompare(b.name);
const MCP_ORDER: Record<MCPSort, (a: MCPServer, b: MCPServer) => number> = {
  name: byName,
  enabled: (a, b) => Number(b.enabled) - Number(a.enabled) || byName(a, b),
};

/** MCP servers: a table (connection, Enabled switch, tools; Test connection, and Edit / Delete in the row menu) with
 * search and sortable headers; New MCP server and Edit open a right Sheet. */
export default function MCPServersView() {
  const [servers, setServers] = useState<MCPServer[] | null>(null);
  // The sheet: undefined while closed, null for a new connection.
  const [editing, setEditing] = useState<MCPServer | null | undefined>(undefined);
  const [removing, setRemoving] = useState<MCPServer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort<MCPSort>>({ key: "name", descending: false });
  async function refresh() {
    setServers((await getMCPServers()).servers);
  }
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(failed(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void act(refresh);
  }, []);
  function edit(server: MCPServer | null) {
    setError(null);
    setEditing(server);
  }
  const text = search.trim().toLowerCase();
  const shown = sortRows(
    (servers ?? []).filter((server) =>
      `${server.name}\n${server.url}\n${server.description}`.toLowerCase().includes(text),
    ),
    sort,
    MCP_ORDER,
  );
  const newServer = (
    <Button disabled={busy} onClick={() => edit(null)}>
      <Plus data-icon="inline-start" />
      New MCP server
    </Button>
  );
  const columns: ColumnDef<MCPServer, unknown>[] = [
    {
      id: "name",
      header: () => sortHeader("Connection", "name", sort, setSort),
      cell: ({ row: { original: server } }) => (
        <div className="max-w-sm">
          <p className="font-medium">{server.name}</p>
          <p className="break-all text-xs text-muted-foreground">{server.url}</p>
          {server.description && <p className="mt-1 text-sm text-muted-foreground">{server.description}</p>}
          <Badge variant="outline" className="mt-2">
            {server.has_token ? "Token saved" : "No authentication"}
          </Badge>
        </div>
      ),
    },
    {
      id: "enabled",
      header: () => sortHeader("Enabled", "enabled", sort, setSort),
      cell: ({ row: { original: server } }) => (
        <Switch
          aria-label={`Enable ${server.name}`}
          checked={server.enabled}
          disabled={busy}
          onCheckedChange={(enabled) =>
            void act(async () => {
              await saveMCPServer(server.id, {
                name: server.name,
                description: server.description,
                url: server.url,
                revision: server.revision,
                enabled,
              });
              await refresh();
            })
          }
        />
      ),
    },
    {
      id: "tools",
      header: "Tools",
      cell: ({ row: { original: server } }) => (
        <div className="max-w-sm">
          {server.last_test_error ? (
            <span className="text-destructive">{server.last_test_error}</span>
          ) : server.last_tested_at ? (
            <details>
              <summary className="cursor-pointer">{server.tools.length} tools · Connection verified</summary>
              <ul className="mt-2 flex list-inside list-disc flex-col gap-2">
                {server.tools.map((tool) => (
                  <li key={tool.name}>
                    <span className="font-medium">{tool.name}</span>
                    <p className="text-xs text-muted-foreground">{tool.description}</p>
                  </li>
                ))}
              </ul>
            </details>
          ) : (
            "Not tested"
          )}
          {server.last_tested_at && (
            <p className="mt-1 text-xs text-muted-foreground">{new Date(server.last_tested_at).toLocaleString()}</p>
          )}
        </div>
      ),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: server } }) => (
        <div className="flex justify-end gap-1">
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Test connection for ${server.name}`}
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const result = await testMCPServer(server);
                await refresh();
                if (result.ok) toast.success(result.message);
                else setError(result.message);
              })
            }
          >
            <Plug data-icon="inline-start" />
            Test connection
          </Button>
          <RowMenu
            name={server.name}
            disabled={busy}
            items={[
              { label: "Edit", icon: Pencil, onSelect: () => edit(server) },
              deleteMenuItem(() => {
                setError(null);
                setRemoving(server);
              }),
            ]}
          />
        </div>
      ),
    },
  ];
  return (
    <div className="flex flex-col gap-6">
      {error && !removing && (
        <Alert variant="destructive">
          <AlertDescription role="alert">{error}</AlertDescription>
        </Alert>
      )}
      {editing !== undefined && (
        <MCPServerSheet
          server={editing}
          onClose={() => setEditing(undefined)}
          onSaved={(name) => {
            setEditing(undefined);
            void act(refresh);
            toast.success(`MCP server “${name}” saved. Test the connection to discover its tools.`);
          }}
        />
      )}
      <DataTable
        columns={columns}
        data={shown}
        loading={servers === null && busy}
        rowId={(server) => server.id}
        search={{ plural: "MCP servers", value: search, onChange: setSearch }}
        actions={newServer}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        empty={
          servers?.length === 0 ? (
            <TableEmpty
              icon={Unplug}
              title="No MCP servers yet"
              description="Add a connection to make external tools available to agents."
            >
              {newServer}
            </TableEmpty>
          ) : servers ? (
            "No MCP servers match."
          ) : (
            "Could not load MCP servers."
          )
        }
      />
      <ConfirmRemove
        removal={
          removing && {
            title: `Delete “${removing.name}”?`,
            description:
              "It and its saved token will be deleted. It will be removed from all agent configurations, and active runs will lose access to this connection.",
            action: "Delete MCP server",
            run: async () => {
              try {
                await deleteMCPServer(removing);
              } catch (e) {
                throw new Error(e instanceof ApiError ? e.message : "MCP operation failed. Try refreshing.");
              }
              setRemoving(null);
              await act(refresh);
              toast.success(`MCP server “${removing.name}” deleted.`);
            },
          }
        }
        onClose={() => setRemoving(null)}
        busy={busy}
      />
    </div>
  );
}
