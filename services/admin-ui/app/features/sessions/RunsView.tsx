import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { getRuns, RUN_DEFAULTS, runPath, topTools, toolUsageLine, type RunPage } from "./runs";
import { getAgents, type AgentSummary } from "~/features/agents/api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyDescription } from "~/components/ui/empty";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const KINDS: Record<string, string> = {
  test: "Agent test",
  test_session: "Test session",
  event_session: "Event session",
  event_task: "Event task",
  native_session: "Native session",
};
// Kinds no longer produced, kept so old run records still read well; not offered in the filter.
const RETIRED_KINDS: Record<string, string> = { claude_code: "Claude Code" };

const STATUSES: Record<string, string> = {
  working: "Working",
  completed: "Completed",
  failed: "Failed",
  stopped: "Stopped",
};
const SELECT = "h-9 rounded-md border bg-background px-2";

export default function RunsView() {
  const [params, setParams] = useSearchParams();
  const value = (key: string) => params.get(key) || RUN_DEFAULTS[key] || "";
  const [sort, order, user, keyPrefix] = [value("sort"), value("order"), value("user"), value("key_prefix")];
  const page = Math.max(1, Number(value("page")) || 1);
  const pageSize = Number(value("page_size")) || 50;
  const filtered = ["kind", "agent_id", "status", "user", "key_prefix"].some((key) => params.get(key));
  const [data, setData] = useState<RunPage | null>(null);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [error, setError] = useState("");
  const query = params.toString();
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    getRuns(new URLSearchParams(query), controller.signal)
      .then((page) => {
        if (!controller.signal.aborted) setData(page);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load runs");
      });
    return () => controller.abort();
  }, [query]);
  useEffect(() => {
    getAgents()
      .then((data) => setAgents(data.agents ?? []))
      .catch(() => setAgents([]));
  }, []);
  /** Set search params (defaults and empty values leave the URL); anything but a page change goes back to page 1. */
  const update = (changes: Record<string, string>) =>
    setParams((current) => {
      if (!("page" in changes)) current.delete("page");
      for (const [key, next] of Object.entries(changes)) {
        if (next && next !== RUN_DEFAULTS[key]) current.set(key, next);
        else current.delete(key);
      }
      return current;
    });
  const sortBy = (column: string) =>
    update(column === sort ? { order: order === "desc" ? "asc" : "desc" } : { sort: column, order: "desc" });
  const sortable = (column: string, label: string) => (
    <TableHead aria-sort={sort === column ? (order === "desc" ? "descending" : "ascending") : undefined}>
      <button type="button" className="inline-flex items-center gap-1 hover:underline" onClick={() => sortBy(column)}>
        {label}
        {sort === column && <span aria-hidden>{order === "desc" ? "↓" : "↑"}</span>}
      </button>
    </TableHead>
  );
  const rows = data?.runs ?? null;
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Run type
          <select
            aria-label="Run type"
            className={SELECT}
            value={value("kind")}
            onChange={(e) => update({ kind: e.target.value })}
          >
            <option value="">All</option>
            {Object.entries(KINDS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Agent
          <select
            aria-label="Agent"
            className={SELECT}
            value={value("agent_id")}
            onChange={(e) => update({ agent_id: e.target.value })}
          >
            <option value="">All</option>
            {value("agent_id") && !agents.some((agent) => agent.id === value("agent_id")) && (
              <option value={value("agent_id")}>{value("agent_id")}</option>
            )}
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Status
          <select
            aria-label="Status"
            className={SELECT}
            value={value("status")}
            onChange={(e) => update({ status: e.target.value })}
          >
            <option value="">All</option>
            {Object.entries(STATUSES).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          User
          <Input
            key={user}
            aria-label="User"
            placeholder="email contains"
            defaultValue={user}
            onKeyDown={(e) => {
              if (e.key === "Enter") update({ user: e.currentTarget.value.trim() });
            }}
            onBlur={(e) => {
              if (e.target.value.trim() !== user) update({ user: e.target.value.trim() });
            }}
          />
        </label>
        {keyPrefix && (
          <Button variant="outline" size="sm" onClick={() => update({ key_prefix: "" })}>
            Clear run filter
          </Button>
        )}
      </div>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : rows === null ? (
        <Skeleton className="h-40 w-full" />
      ) : rows.length === 0 ? (
        <Empty className="border">
          <EmptyDescription>
            {total > 0 ? "No runs on this page." : filtered ? "No runs match these filters." : "No recorded runs yet."}
          </EmptyDescription>
        </Empty>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Run</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Status</TableHead>
                {sortable("tokens", "Tokens")}
                <TableHead>Tools</TableHead>
                {sortable("started", "Started")}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id ?? row.run_key}>
                  <TableCell className="max-w-md">
                    <Link
                      className="underline underline-offset-4"
                      to={runPath(row.run_key, row.kind === "expert_call" ? row.call_id : undefined)}
                    >
                      {row.title || row.run_key}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">
                      {row.kind === "expert_call"
                        ? `Called as expert by ${row.caller ?? "another agent"}`
                        : row.kind
                          ? (KINDS[row.kind] ?? RETIRED_KINDS[row.kind] ?? row.kind)
                          : "run"}
                    </Badge>
                  </TableCell>
                  <TableCell>{row.agent ?? row.agent_id}</TableCell>
                  <TableCell>{row.user ?? "—"}</TableCell>
                  <TableCell>{row.status ?? "working"}</TableCell>
                  <TableCell>{row.total_tokens.toLocaleString()}</TableCell>
                  <TableCell className="max-w-xs whitespace-normal" title={toolUsageLine(row.tool_counts)}>
                    {topTools(row.tool_counts)}
                  </TableCell>
                  <TableCell>{new Date(row.started_at).toLocaleString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {data && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <span className="text-muted-foreground">
            Page {page} of {pageCount} · {total.toLocaleString()} {total === 1 ? "run" : "runs"}
          </span>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-muted-foreground">
              Rows per page
              <select
                aria-label="Rows per page"
                className="h-8 rounded-md border bg-background px-2 text-foreground"
                value={String(pageSize)}
                onChange={(e) => update({ page_size: e.target.value })}
              >
                {[25, 50, 100].map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
              </select>
            </label>
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => update({ page: String(page - 1) })}>
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pageCount}
              onClick={() => update({ page: String(page + 1) })}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
