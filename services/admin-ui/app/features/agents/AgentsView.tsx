import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams, useLocation } from "react-router";
import { ArrowRight, Copy, Play, Sparkles, Trash2 } from "lucide-react";
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
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Skeleton } from "~/components/ui/skeleton";

export default function AgentsView() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { hash } = useLocation();
  const [agents, setAgents] = useState<AgentSummary[]>();
  const [query, setQuery] = useState("");
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
  const filtered = agents?.filter((a) =>
    `${a.name} ${a.description} ${countsSummary(a.counts)}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <section className="flex flex-col gap-6" aria-label="Agent list">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Input
          aria-label="Find agents"
          placeholder="Find an agent…"
          className="max-w-sm"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setCreating(true)} disabled={creating}>
            <Sparkles data-icon="inline-start" />
            New agent
          </Button>
        </div>
      </div>
      {creating && (
        <section aria-label="New agent" className="flex flex-col gap-3">
          <AgentWizard onClose={() => setCreating(false)} onCreated={(id) => navigate(`/agents/${id}`)} />
        </section>
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
      {!agents ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                <TableHead>Resources</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered?.map((a) => (
                <TableRow key={a.id}>
                  <TableCell className="max-w-sm whitespace-normal">
                    <Link className="font-medium underline-offset-4 hover:underline" to={`/agents/${a.id}`}>
                      {a.name}
                    </Link>
                    {a.expert && (
                      <>
                        {" "}
                        <Badge variant="secondary">Expert</Badge>
                      </>
                    )}
                    {a.original_name && (
                      <p className="mt-1 text-xs text-muted-foreground">Clone of {a.original_name}</p>
                    )}
                    {a.description && (
                      <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{a.description}</p>
                    )}
                  </TableCell>
                  <TableCell className="max-w-xs whitespace-normal">{countsSummary(a.counts)}</TableCell>
                  <TableCell>{a.model_name ?? "Not configured"}</TableCell>
                  <TableCell>
                    <Badge variant={a.enabled ? "secondary" : "outline"}>{a.enabled ? "Enabled" : "Disabled"}</Badge>
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={!a.enabled}
                      aria-label={`Run ${a.name}`}
                      title={a.enabled ? "Run" : "Enable the agent to run it"}
                      onClick={() => chat(a.id)}
                    >
                      <Play />
                    </Button>
                    <Button variant="ghost" size="icon" asChild>
                      <Link to={`/agents/${a.id}`} aria-label={`Open ${a.name}`}>
                        <ArrowRight />
                      </Link>
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={busy}
                      aria-label={`Clone ${a.name}`}
                      onClick={() => void cloneRow(a)}
                    >
                      <Copy />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={busy}
                      aria-label={`Delete ${a.name}`}
                      onClick={() => {
                        setError("");
                        setRemoving(a);
                      }}
                    >
                      <Trash2 data-icon="inline-start" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {filtered?.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                    No agents match your search.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}
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
