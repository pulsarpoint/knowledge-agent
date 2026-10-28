import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams, useLocation } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { Bot, Copy, Play, Sparkles } from "lucide-react";
import AgentWizard from "./AgentWizard";
import { toast } from "sonner";
import { cloneAgent } from "./clones";
import {
  agentPath,
  countsSummary,
  deleteAgent,
  describeHistory,
  getAgentHistory,
  getAgents,
  type AgentSummary,
} from "./api";
import ConfirmRemove from "~/components/ConfirmRemove";
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import RowMenu, { deleteMenuItem } from "~/components/RowMenu";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";

type AgentSort = "name" | "model" | "status";
const byName = (a: AgentSummary, b: AgentSummary) => a.name.localeCompare(b.name);
const AGENT_ORDER: Record<AgentSort, (a: AgentSummary, b: AgentSummary) => number> = {
  name: byName,
  model: (a, b) => (a.model_name ?? "").localeCompare(b.model_name ?? "") || byName(a, b),
  status: (a, b) => Number(b.enabled) - Number(a.enabled) || byName(a, b),
};

/** Agents: a table (name with its description, resources, model, Enabled / Disabled; Run, and Clone / Delete in the row
 * menu) with search and sortable headers; New agent opens the wizard in a right Sheet. */
export default function AgentsView() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { hash } = useLocation();
  const [agents, setAgents] = useState<AgentSummary[]>();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort<AgentSort>>({ key: "name", descending: false });
  const [creating, setCreating] = useState(false);
  const [removing, setRemoving] = useState<AgentSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  // What would go with the agent: undefined while checking, '' without history (or when it could not be checked).
  const [history, setHistory] = useState<string>();
  useEffect(() => {
    let live = true;
    getAgents()
      .then((data) => {
        if (live) {
          setAgents(data.agents);
          setError("");
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [reload]);
  useEffect(() => {
    if (!removing) return;
    let live = true;
    setHistory(undefined);
    getAgentHistory(removing.id)
      .then((value) => {
        if (live) setHistory(describeHistory(value));
      })
      .catch(() => {
        if (live) setHistory("");
      });
    return () => {
      live = false;
    };
  }, [removing]);
  // An old link (/agents?agent=<id>&view=…): the agent's page maps the view to its sub-page.
  const legacy = params.get("agent");
  if (legacy) {
    const rest = new URLSearchParams(params);
    rest.delete("agent");
    const query = rest.toString();
    return <Navigate replace to={`/agents/${encodeURIComponent(legacy)}${query ? `?${query}` : ""}${hash}`} />;
  }
  async function remove() {
    if (!removing) return;
    setBusy(true);
    setError("");
    const withHistory = Boolean(history);
    try {
      await deleteAgent(removing.id, removing.revision, withHistory);
      setAgents((current) => current?.filter((agent) => agent.id !== removing.id));
      setRemoving(null);
      toast.success(withHistory ? `Agent ${removing.name} and its history deleted` : `Agent ${removing.name} deleted`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete the agent.");
      // History may have appeared since the dialog opened; show it (next to the reason) so it can be deleted too.
      if (!withHistory)
        await getAgentHistory(removing.id)
          .then((value) => {
            const found = describeHistory(value);
            if (found) setHistory(found);
          })
          .catch(() => undefined);
      throw reason; // the dialog stays open with the reason
    } finally {
      setBusy(false);
    }
  }
  async function cloneRow(agent: AgentSummary) {
    setBusy(true);
    setError("");
    try {
      const created = await cloneAgent(agent.id);
      toast.success(`Clone created: ${created.name}`);
      navigate(`/agents/${encodeURIComponent(created.id)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not clone the agent.");
    } finally {
      setBusy(false);
    }
  }
  const chat = (id: string) => navigate(agentPath(id, "chat"));
  const search = query.trim().toLowerCase();
  const shown = sortRows(
    (agents ?? []).filter((a) =>
      `${a.name} ${a.description} ${countsSummary(a.counts)}`.toLowerCase().includes(search),
    ),
    sort,
    AGENT_ORDER,
  );
  const columns: ColumnDef<AgentSummary, unknown>[] = [
    {
      id: "name",
      header: () => sortHeader("Agent", "name", sort, setSort),
      cell: ({ row: { original: a } }) => (
        <div className="max-w-sm">
          <Link className="font-medium underline-offset-4 hover:underline" to={`/agents/${a.id}`}>
            {a.name}
          </Link>
          {a.expert && (
            <>
              {" "}
              <Badge variant="secondary">Expert</Badge>
            </>
          )}
          {a.original_name && <p className="mt-1 text-xs text-muted-foreground">Clone of {a.original_name}</p>}
          {a.description && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{a.description}</p>}
        </div>
      ),
    },
    { id: "resources", header: "Resources", cell: ({ row: { original: a } }) => countsSummary(a.counts) },
    {
      id: "model",
      header: () => sortHeader("Model", "model", sort, setSort),
      cell: ({ row: { original: a } }) => a.model_name ?? "Not configured",
    },
    {
      id: "status",
      header: () => sortHeader("Status", "status", sort, setSort),
      cell: ({ row: { original: a } }) => (
        <Badge variant={a.enabled ? "secondary" : "outline"}>{a.enabled ? "Enabled" : "Disabled"}</Badge>
      ),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: a } }) => (
        <div className="flex justify-end gap-1">
          <Button
            variant="ghost"
            size="sm"
            disabled={!a.enabled}
            aria-label={`Run ${a.name}`}
            title={a.enabled ? undefined : "Enable the agent to run it"}
            onClick={() => chat(a.id)}
          >
            <Play data-icon="inline-start" />
            Run
          </Button>
          <RowMenu
            name={a.name}
            disabled={busy}
            items={[
              { label: "Clone", icon: Copy, onSelect: () => void cloneRow(a) },
              deleteMenuItem(() => {
                setError("");
                setRemoving(a);
              }),
            ]}
          />
        </div>
      ),
    },
  ];
  const newAgent = (
    <Button onClick={() => setCreating(true)} disabled={creating}>
      <Sparkles data-icon="inline-start" />
      New agent
    </Button>
  );
  return (
    <section className="flex flex-col gap-6" aria-label="Agent list">
      {creating && (
        <AgentWizard open onClose={() => setCreating(false)} onCreated={(id) => navigate(`/agents/${id}`)} />
      )}
      {error && !removing && (
        <Alert variant="destructive">
          <AlertDescription>
            {error}
            <Button variant="outline" size="sm" onClick={() => setReload((n) => n + 1)}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={shown}
        loading={!agents && !error}
        rowId={(a) => a.id}
        search={{ plural: "agents", value: query, onChange: setQuery }}
        actions={newAgent}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        empty={
          agents?.length === 0 ? (
            <TableEmpty
              icon={Bot}
              title="No agents yet"
              description="An agent answers questions and does tasks with the resources you give it."
            >
              {newAgent}
            </TableEmpty>
          ) : agents ? (
            "No agents match your search."
          ) : (
            "Could not load agents."
          )
        }
      />
      <ConfirmRemove
        removal={
          removing && {
            title: `Delete “${removing.name}”?`,
            description:
              "Its saved configuration is deleted. Shared servers, code projects, skills, models, and documents will be kept.",
            action: history === undefined ? "Checking history…" : history ? "Delete agent and history" : "Delete agent",
            run: remove,
          }
        }
        onClose={() => {
          setRemoving(null);
          setError("");
        }}
        busy={busy || history === undefined}
        error={removing ? error || null : null}
      >
        {history && (
          <div className="flex flex-col gap-1 text-sm">
            <p>This agent has history that will be deleted with it:</p>
            <p className="font-medium">{history}</p>
            <p className="text-muted-foreground">
              Saved documents are kept. To keep the history instead, disable the agent.
            </p>
          </div>
        )}
      </ConfirmRemove>
    </section>
  );
}
