import { useEffect, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getMCPServers, saveMCPServer, testMCPServer, deleteMCPServer, type MCPServer } from "./mcp";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "~/components/ui/card";
import { Checkbox } from "~/components/ui/checkbox";
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription } from "~/components/ui/empty";
import { Field, FieldGroup, FieldLabel, FieldDescription } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import { Switch } from "~/components/ui/switch";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";

export default function MCPServersView() {
  const [servers, setServers] = useState<MCPServer[] | null>(null);
  const [editing, setEditing] = useState<{
    server: MCPServer | null;
    name: string;
    description: string;
    url: string;
    enabled: boolean;
    token: string;
    removeToken: boolean;
  } | null>(null);
  const [removing, setRemoving] = useState<MCPServer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  async function refresh() {
    setServers((await getMCPServers()).servers);
  }
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "MCP operation failed. Try refreshing.");
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void act(refresh);
  }, []);
  function edit(server: MCPServer | null) {
    setError(null);
    setNotice(null);
    setEditing({
      server,
      name: server?.name ?? "",
      description: server?.description ?? "",
      url: server?.url ?? "",
      enabled: server?.enabled ?? true,
      token: "",
      removeToken: false,
    });
  }
  return (
    <div className="flex flex-col gap-6">
      <div className="flex justify-between gap-3">
        <p className="text-sm text-muted-foreground">{servers?.length ?? 0} connections</p>
        <div className="flex gap-2">
          <Button variant="outline" disabled={busy || !!editing} onClick={() => void act(refresh)}>
            <RefreshCw data-icon="inline-start" />
            Refresh
          </Button>
          <Button disabled={busy || !!editing} onClick={() => edit(null)}>
            <Plus data-icon="inline-start" />
            Add MCP server
          </Button>
        </div>
      </div>
      {error && !removing && (
        <Alert variant="destructive">
          <AlertDescription role="alert">{error}</AlertDescription>
        </Alert>
      )}
      {notice && (
        <Alert>
          <AlertDescription role="status">{notice}</AlertDescription>
        </Alert>
      )}
      {editing && (
        <Card>
          <CardHeader>
            <CardTitle>{editing.server ? "Edit MCP server" : "Add MCP server"}</CardTitle>
            <CardDescription>
              Streamable HTTP with an optional bearer token. Save, test the connection, then select it in agent
              settings.
            </CardDescription>
          </CardHeader>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void act(async () => {
                await saveMCPServer(editing.server?.id ?? null, {
                  name: editing.name,
                  description: editing.description,
                  url: editing.url,
                  enabled: editing.enabled,
                  ...(editing.server ? { revision: editing.server.revision } : {}),
                  ...(editing.removeToken ? { token: null } : editing.token ? { token: editing.token } : {}),
                });
                setEditing(null);
                await refresh();
                setNotice("MCP server saved. Test the connection to discover its tools.");
              });
            }}
          >
            <CardContent className="pb-6">
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
                    Use an address reachable from Docker. For a service on this machine, use host.docker.internal
                    instead of localhost.
                  </FieldDescription>
                </Field>
                <Field>
                  <FieldLabel htmlFor="mcp-token">
                    {editing.server?.has_token ? "Replace bearer token" : "Bearer token (optional)"}
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
                    {editing.server?.has_token
                      ? "Token is saved encrypted. Leave blank to keep it. Changing the endpoint requires replacing or removing the token."
                      : "Stored encrypted. Leave blank for an endpoint that does not require authentication."}
                  </FieldDescription>
                </Field>
                {editing.server?.has_token && (
                  <Field orientation="horizontal">
                    <Checkbox
                      id="mcp-remove-token"
                      checked={editing.removeToken}
                      disabled={busy}
                      onCheckedChange={(checked) =>
                        setEditing({ ...editing, removeToken: checked === true, token: "" })
                      }
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
            </CardContent>
            <CardFooter className="flex gap-3">
              <Button disabled={busy}>Save MCP server</Button>
              <Button type="button" variant="outline" disabled={busy} onClick={() => setEditing(null)}>
                Cancel
              </Button>
            </CardFooter>
          </form>
        </Card>
      )}
      {servers === null && busy && <Skeleton className="h-40 w-full" />}
      {servers?.length === 0 && !editing && (
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>No MCP servers yet</EmptyTitle>
            <EmptyDescription>
              Add a connection to make external tools available to Code search and SSH agents.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
      {!!servers?.length && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Connection</TableHead>
              <TableHead>Enabled</TableHead>
              <TableHead>Tools</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {servers.map((server) => (
              <TableRow key={server.id}>
                <TableCell className="max-w-sm whitespace-normal">
                  <p className="font-medium">{server.name}</p>
                  <p className="break-all text-xs text-muted-foreground">{server.url}</p>
                  {server.description && <p className="mt-1 text-sm text-muted-foreground">{server.description}</p>}
                  <Badge variant="outline" className="mt-2">
                    {server.has_token ? "Token saved" : "No authentication"}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Switch
                    aria-label={`Enable ${server.name}`}
                    checked={server.enabled}
                    disabled={busy || !!editing}
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
                </TableCell>
                <TableCell className="max-w-sm whitespace-normal">
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
                    <p className="mt-1 text-xs text-muted-foreground">
                      {new Date(server.last_tested_at).toLocaleString()}
                    </p>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy || !!editing}
                      onClick={() =>
                        void act(async () => {
                          const result = await testMCPServer(server);
                          await refresh();
                          if (result.ok) setNotice(result.message);
                          else setError(result.message);
                        })
                      }
                    >
                      Test connection
                    </Button>
                    <Button size="sm" variant="outline" disabled={busy || !!editing} onClick={() => edit(server)}>
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy || !!editing}
                      onClick={() => {
                        setError(null);
                        setRemoving(server);
                      }}
                    >
                      Remove
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <AlertDialog
        open={!!removing}
        onOpenChange={(open) => {
          if (!open && !busy) setRemoving(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove MCP server?</AlertDialogTitle>
            <AlertDialogDescription>
              {removing?.name} and its saved token will be deleted. It will be removed from all agent configurations,
              and active runs will lose access to this connection.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              onClick={(event) => {
                event.preventDefault();
                if (removing)
                  void act(async () => {
                    await deleteMCPServer(removing);
                    setRemoving(null);
                    await refresh();
                    setNotice("MCP server removed.");
                  });
              }}
            >
              Remove MCP server
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
