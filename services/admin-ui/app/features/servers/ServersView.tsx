import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { FileText, KeyRound, Pencil, Plug, Plus, Server } from "lucide-react";
import { ApiError } from "~/lib/api";
import { deleteServer, getServers, type ServerAsset } from "./api";
import { getSSH, testSSH } from "./operations";
import ServerForm from "./ServerForm";
import ServerSSHForm from "./ServerSSHForm";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import ConfirmRemove from "~/components/ConfirmRemove";
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Spinner } from "~/components/ui/spinner";

type ServerSort = "name" | "hostname" | "ssh";
const byName = (a: ServerAsset, b: ServerAsset) => a.name.localeCompare(b.name);
const SERVER_ORDER: Record<ServerSort, (a: ServerAsset, b: ServerAsset) => number> = {
  name: byName,
  hostname: (a, b) => a.hostname.localeCompare(b.hostname) || byName(a, b),
  ssh: (a, b) => Number(b.ssh_allowed) - Number(a.ssh_allowed) || byName(a, b),
};

/** Servers: a table (server and description, hostname, tags, agent SSH; SSH settings, Test SSH, Documents, Edit and
 * Delete in the row menu) with search and sortable headers; New server, Edit and SSH settings open right Sheets. */
export default function ServersView() {
  const [servers, setServers] = useState<ServerAsset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort<ServerSort>>({ key: "name", descending: false });
  const [form, setForm] = useState<{ server: ServerAsset | null } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ServerAsset | null>(null);
  const [sshServer, setSshServer] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ name: string; ok: boolean; message: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getServers(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          setServers(data.servers);
          setError(null);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setError(error instanceof ApiError ? error.message : "Could not load servers. Try refreshing.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);

  function openForm(server: ServerAsset | null) {
    setForm({ server });
  }
  async function confirmDelete() {
    if (deleteTarget === null || deleting) return;
    const target = deleteTarget;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteServer(target.id, target.revision);
      setServers((current) => current?.filter((server) => server.id !== target.id) ?? null);
      setDeleteTarget(null);
      toast.success(`Server “${target.name}” deleted.`);
      setRefresh((value) => value + 1);
    } catch (error) {
      setDeleteError(error instanceof ApiError ? error.message : "Could not delete the server. Try again.");
      throw error; // the dialog stays open with the reason
    } finally {
      setDeleting(false);
    }
  }
  async function testConnection(server: ServerAsset) {
    setTesting(server.id);
    setTestResult(null);
    try {
      const saved = await getSSH(server.id);
      const result = await testSSH(server.id, {
        revision: saved.revision,
        username: saved.username,
        port: saved.port,
        allow_sudo: saved.allow_sudo,
        known_hosts: saved.known_hosts,
      });
      setTestResult({ name: server.name, ok: result.ok, message: result.message });
    } catch (error) {
      setTestResult({
        name: server.name,
        ok: false,
        message: error instanceof ApiError ? error.message : "Could not test SSH.",
      });
    } finally {
      setTesting(null);
    }
  }
  const text = query.trim().toLowerCase();
  const shown = sortRows(
    servers?.filter((server) =>
      [server.name, server.hostname, server.description, ...server.tags].some((value) =>
        value.toLowerCase().includes(text),
      ),
    ) ?? [],
    sort,
    SERVER_ORDER,
  );
  const newServer = (
    <Button disabled={loading || error !== null || servers === null || form !== null} onClick={() => openForm(null)}>
      <Plus data-icon="inline-start" />
      New server
    </Button>
  );
  const columns: ColumnDef<ServerAsset, unknown>[] = [
    {
      id: "name",
      header: () => sortHeader("Server", "name", sort, setSort),
      cell: ({ row: { original: server } }) => (
        <div className="max-w-sm">
          <p className="break-words font-medium">
            <Link className="underline-offset-4 hover:underline" to={`/servers/${server.id}`}>
              {server.name}
            </Link>
          </p>
          <p className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">
            {server.description || "No description yet"}
          </p>
        </div>
      ),
    },
    {
      id: "hostname",
      header: () => sortHeader("Hostname / IP", "hostname", sort, setSort),
      cell: ({ row: { original: server } }) => <span className="break-all font-mono text-sm">{server.hostname}</span>,
    },
    {
      id: "tags",
      header: "Tags",
      cell: ({ row: { original: server } }) => (
        <div className="flex min-w-40 max-w-xs flex-wrap gap-1">
          {server.tags.map((tag) => (
            <Badge key={tag} variant="secondary" className="max-w-full whitespace-normal break-all">
              {tag}
            </Badge>
          ))}
          {server.tags.length === 0 && <span className="text-sm text-muted-foreground">No tags</span>}
        </div>
      ),
    },
    {
      id: "ssh",
      header: () => sortHeader("Agent SSH", "ssh", sort, setSort),
      cell: ({ row: { original: server } }) => (
        <Badge variant={server.ssh_allowed ? "secondary" : "outline"}>
          {server.ssh_allowed ? "Enabled" : "Disabled"}
        </Badge>
      ),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: server } }) => (
        <div className="flex items-center justify-end">
          {testing === server.id && <Spinner className="mr-2 inline" aria-label={`Testing SSH on ${server.name}`} />}
          <RowMenu
            name={server.name}
            items={[
              {
                label: "SSH settings",
                icon: KeyRound,
                disabled: loading || form !== null || sshServer !== null,
                onSelect: () => {
                  setTestResult(null);
                  setSshServer(server.id);
                },
              },
              {
                label: "Test SSH",
                icon: Plug,
                disabled: !server.ssh_allowed || testing !== null || sshServer !== null,
                hint: server.ssh_allowed ? undefined : "Enable agent SSH to test",
                onSelect: () => void testConnection(server),
              },
              { label: "Documents", icon: FileText, to: `/documents?server_id=${server.id}` },
              {
                label: "Edit",
                icon: Pencil,
                disabled: loading || error !== null || form !== null,
                onSelect: () => openForm(server),
              },
              {
                ...deleteMenuItem(() => {
                  setDeleteTarget(server);
                  setDeleteError(null);
                }),
                disabled: loading || error !== null || form !== null,
              },
            ]}
          />
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Server inventory unavailable</AlertTitle>
          <AlertDescription>
            {error}
            <Button variant="outline" size="sm" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {testResult && (
        <Alert variant={testResult.ok ? "default" : "destructive"}>
          <AlertTitle className="break-words">
            {testResult.name}: SSH test {testResult.ok ? "passed" : "failed"}
          </AlertTitle>
          <AlertDescription role="status">
            {testResult.message}
            {!testResult.ok && " Open SSH settings to fix the configuration."}
          </AlertDescription>
        </Alert>
      )}
      {sshServer && (
        <ServerSSHForm
          id={sshServer}
          onClose={() => {
            setSshServer(null);
            setRefresh((value) => value + 1);
          }}
        />
      )}
      {form && (
        <ServerForm
          key={form.server?.id ?? "new"}
          server={form.server}
          onClose={() => {
            setForm(null);
            setRefresh((value) => value + 1);
          }}
          onSaved={() => {
            setForm(null);
            setQuery("");
            toast.success("Server saved. Linked services now use its updated details.");
            setRefresh((value) => value + 1);
          }}
        />
      )}
      <DataTable
        columns={columns}
        data={shown}
        loading={servers === null && loading}
        rowId={(server) => server.id}
        search={{ plural: "servers", value: query, onChange: setQuery }}
        actions={newServer}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        empty={
          servers?.length === 0 ? (
            <TableEmpty
              icon={Server}
              title="Build your server inventory"
              description="Register hosts with descriptions and tags, then connect them to services for agent investigations."
            >
              {newServer}
            </TableEmpty>
          ) : servers ? (
            "No servers match."
          ) : (
            "Could not load servers."
          )
        }
      />
      <ConfirmRemove
        removal={
          deleteTarget && {
            title: `Delete “${deleteTarget.name}”?`,
            description: `This permanently removes the server record for ${deleteTarget.hostname} from the inventory. Linked service deployments must be removed first. This cannot be undone.`,
            action: "Delete server",
            run: confirmDelete,
          }
        }
        onClose={() => {
          setDeleteTarget(null);
          setDeleteError(null);
        }}
        busy={deleting}
        error={deleteError}
      />
    </div>
  );
}
