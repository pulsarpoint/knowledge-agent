import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  getRuns,
  nearestPageSize,
  RUN_DEFAULTS,
  runPath,
  topTools,
  toolUsageLine,
  type RunPage,
  type RunSummary,
} from "./runs";
import { getAgents, type AgentSummary } from "~/features/agents/api";
import { getSchedules } from "~/features/events/schedules";
import type { ColumnDef } from "@tanstack/react-table";
import { Activity, RefreshCw } from "lucide-react";
import FiltersSheet from "~/components/FiltersSheet";
import DataTable, { FILTER_WIDTH, sortHeader, TableEmpty, type Sort } from "~/components/DataTable";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";

const KINDS: Record<string, string> = {
  test: "Agent test",
  event_session: "Event run",
  chat_session: "Chat",
  event_task: "Event task",
  native_session: "Native run",
  review_session: "Session review",
};
// Kinds no longer produced, kept so old run records still read well; not offered in the filter.
const RETIRED_KINDS: Record<string, string> = { claude_code: "Claude Code" };

const STATUSES: Record<string, string> = {
  working: "Working",
  completed: "Completed",
  failed: "Failed",
  stopped: "Stopped",
  retrying: "Retrying",
};
const ALL = "all";

/** A toolbar filter on a search param: an unlabelled Select named by `label`, "All …" first. */
function FilterSelect({
  label,
  all,
  value,
  options,
  onChange,
}: {
  label: string;
  all: string;
  value: string;
  options: [string, string][];
  onChange: (value: string) => void;
}) {
  return (
    <Select value={value || ALL} onValueChange={(next) => onChange(next === ALL ? "" : next)}>
      <SelectTrigger aria-label={label} className={FILTER_WIDTH}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectItem value={ALL}>{all}</SelectItem>
          {options.map(([key, text]) => (
            <SelectItem key={key} value={key}>
              {text}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

/** The filters behind the Filters button (type, agent and status stay in the toolbar row). */
const HIDDEN_FILTERS = ["has_output", "schedule_id", "user"];

/** Activity → Runs: every recorded agent run in a table paged by the server, with filters in the URL (type, agent,
 * status in the toolbar row; output, schedule, user in the Filters sheet), sortable Tokens and Started headers, and
 * Refresh (runs come in on their own). */
export default function RunsView() {
  const [params, setParams] = useSearchParams();
  const value = (key: string) => params.get(key) || RUN_DEFAULTS[key] || "";
  const [sort, order, user, keyPrefix] = [value("sort"), value("order"), value("user"), value("key_prefix")];
  const page = Math.max(1, Number(value("page")) || 1);
  const pageSize = nearestPageSize(params.get("page_size"));
  const filtered = ["kind", "agent_id", "status", "has_output", "schedule_id", "user", "key_prefix"].some((key) =>
    params.get(key),
  );
  const [data, setData] = useState<RunPage | null>(null);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [schedules, setSchedules] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const query = params.toString();
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    setData(null);
    getRuns(new URLSearchParams(query), controller.signal)
      .then((page) => {
        if (!controller.signal.aborted) setData(page);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load runs");
      });
    return () => controller.abort();
  }, [query, reload]);
  useEffect(() => {
    getAgents()
      .then((data) => setAgents(data.agents ?? []))
      .catch(() => setAgents([]));
    getSchedules()
      .then((data) => setSchedules(data.schedules ?? []))
      .catch(() => setSchedules([]));
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
  /** A new column sorts newest/largest first; the same column again flips the order. */
  const shownSort: Sort = { key: sort, descending: order === "desc" };
  const header = (label: string, column: string) =>
    sortHeader(label, column, shownSort, (next) =>
      update(next.key === sort ? { order: order === "desc" ? "asc" : "desc" } : { sort: next.key, order: "desc" }),
    );
  const agentId = value("agent_id");
  const scheduleId = value("schedule_id");
  const columns: ColumnDef<RunSummary, unknown>[] = [
    {
      id: "run",
      header: "Run",
      cell: ({ row: { original: row } }) => (
        <Link
          className="max-w-md underline underline-offset-4"
          to={runPath(row.run_key, row.kind === "expert_call" ? row.call_id : undefined)}
        >
          {row.title || row.run_key}
        </Link>
      ),
    },
    {
      id: "type",
      header: "Type",
      cell: ({ row: { original: row } }) => (
        <Badge variant="secondary">
          {row.kind === "expert_call"
            ? `Called as expert by ${row.caller ?? "another agent"}`
            : row.kind === "review_session"
              ? `Review of ${agents.find((agent) => agent.id === row.agent_id)?.name ?? row.agent_id}`
              : row.kind
                ? (KINDS[row.kind] ?? RETIRED_KINDS[row.kind] ?? row.kind)
                : "run"}
        </Badge>
      ),
    },
    { id: "agent", header: "Agent", cell: ({ row: { original: row } }) => row.agent ?? row.agent_id },
    { id: "user", header: "User", cell: ({ row: { original: row } }) => row.user ?? "—" },
    { id: "status", header: "Status", cell: ({ row: { original: row } }) => row.status ?? "working" },
    {
      id: "tokens",
      header: () => header("Tokens", "tokens"),
      cell: ({ row: { original: row } }) => row.total_tokens.toLocaleString(),
    },
    {
      id: "tools",
      header: "Tools",
      cell: ({ row: { original: row } }) => (
        <span className="max-w-xs" title={toolUsageLine(row.tool_counts)}>
          {topTools(row.tool_counts)}
        </span>
      ),
    },
    {
      id: "started",
      header: () => header("Started", "started"),
      cell: ({ row: { original: row } }) => new Date(row.started_at).toLocaleString(),
    },
  ];
  return (
    <div className="flex flex-col gap-4">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={data?.runs ?? []}
        loading={data === null && !error}
        rowId={(row) => String(row.id ?? row.run_key)}
        sort={shownSort}
        total={data?.total ?? 0}
        page={page}
        pageSize={pageSize}
        onPageChange={(next) => update({ page: String(next) })}
        onPageSizeChange={(size) => update({ page_size: String(size) })}
        toolbar={
          <>
            <FilterSelect
              label="Run type"
              all="All run types"
              value={value("kind")}
              options={Object.entries(KINDS)}
              onChange={(kind) => update({ kind })}
            />
            <FilterSelect
              label="Agent"
              all="All agents"
              value={agentId}
              options={[
                ...(agentId && !agents.some((agent) => agent.id === agentId)
                  ? ([[agentId, agentId]] as [string, string][])
                  : []),
                ...agents.map((agent): [string, string] => [agent.id, agent.name]),
              ]}
              onChange={(agent_id) => update({ agent_id })}
            />
            <FilterSelect
              label="Status"
              all="All statuses"
              value={value("status")}
              options={Object.entries(STATUSES)}
              onChange={(status) => update({ status })}
            />
            <FiltersSheet
              active={HIDDEN_FILTERS.filter((key) => params.get(key)).length}
              onReset={() => update(Object.fromEntries(HIDDEN_FILTERS.map((key) => [key, ""])))}
            >
              <FilterSelect
                label="Output"
                all="With or without output"
                value={value("has_output")}
                options={[
                  ["true", "Has output"],
                  ["false", "No output"],
                ]}
                onChange={(has_output) => update({ has_output })}
              />
              <FilterSelect
                label="Schedule"
                all="All schedules"
                value={scheduleId}
                options={[
                  ...(scheduleId && !schedules.some((schedule) => schedule.id === scheduleId)
                    ? ([[scheduleId, "(deleted)"]] as [string, string][])
                    : []),
                  ...schedules.map((schedule): [string, string] => [schedule.id, schedule.name]),
                ]}
                onChange={(schedule_id) => update({ schedule_id })}
              />
              <Input
                key={user}
                type="search"
                aria-label="User"
                placeholder="User email contains"
                className={FILTER_WIDTH}
                defaultValue={user}
                onKeyDown={(e) => {
                  if (e.key === "Enter") update({ user: e.currentTarget.value.trim() });
                }}
                onBlur={(e) => {
                  if (e.target.value.trim() !== user) update({ user: e.target.value.trim() });
                }}
              />
            </FiltersSheet>
            {keyPrefix && (
              <Button variant="outline" onClick={() => update({ key_prefix: "" })}>
                Clear run filter
              </Button>
            )}
          </>
        }
        actions={
          <Button variant="outline" onClick={() => setReload((count) => count + 1)}>
            <RefreshCw data-icon="inline-start" />
            Refresh
          </Button>
        }
        empty={
          error ? (
            "Could not load runs."
          ) : (data?.total ?? 0) > 0 ? (
            "No runs on this page."
          ) : filtered ? (
            "No runs match these filters."
          ) : (
            <TableEmpty
              icon={Activity}
              title="No recorded runs yet"
              description="Chats, event runs and agent tests show up here as they run."
            />
          )
        }
      />
    </div>
  );
}
