import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Link, useSearchParams } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { Network, RefreshCw } from "lucide-react";
import CodeGraphExplorer from "./CodeGraphExplorer";
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import RowMenu from "~/components/RowMenu";
import { getCodeProjects, processCodeProject, type CodeProject } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import { Spinner } from "~/components/ui/spinner";

const active = (project: CodeProject) =>
  ["queued", "running"].includes(project.processing?.status ?? "") ||
  ["queued", "running", "waiting_input"].includes(project.sync_status ?? "");
const graphStatus = (project: CodeProject) =>
  project.processing && ["queued", "running", "failed"].includes(project.processing.status)
    ? project.processing.status
    : (project.code_index?.status ?? "Not generated");
const errorMessage = (reason: unknown) =>
  reason instanceof Error ? reason.message : "Could not reach administration.";

type GraphSort = "name" | "graph" | "generated";
const byName = (a: CodeProject, b: CodeProject) => a.name.localeCompare(b.name);
const GRAPH_ORDER: Record<GraphSort, (a: CodeProject, b: CodeProject) => number> = {
  name: byName,
  graph: (a, b) => graphStatus(a).localeCompare(graphStatus(b)) || byName(a, b),
  generated: (a, b) =>
    (a.code_index?.processed_at ?? "").localeCompare(b.code_index?.processed_at ?? "") || byName(a, b),
};

/** Every code project's graph in a table (status, counts, languages, last generated; View graph or Generate graph, and
 * Regenerate in the row menu) with search, sortable headers and Refresh; the explorer opens below. */
export default function CodeGraphsView() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("project");
  const [projects, setProjects] = useState<CodeProject[] | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort<GraphSort>>({ key: "name", descending: false });

  useEffect(() => {
    const controller = new AbortController();
    getCodeProjects(controller.signal)
      .then((result) => setProjects(result.projects))
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [refresh]);

  const select = (id: string | null) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (id) next.set("project", id);
      else next.delete("project");
      return next;
    });

  async function generate(project: CodeProject) {
    setBusy(true);
    setError("");
    try {
      await processCodeProject(project.id);
      toast.success(`Code graph generation queued for ${project.name}.`);
      setRefresh((n) => n + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  const open = projects?.find((project) => project.id === selected && project.code_index);
  const text = search.trim().toLowerCase();
  const shown = sortRows(
    (projects ?? []).filter((project) => project.name.toLowerCase().includes(text)),
    sort,
    GRAPH_ORDER,
  );
  const columns: ColumnDef<CodeProject, unknown>[] = [
    {
      id: "name",
      header: () => sortHeader("Code project", "name", sort, setSort),
      cell: ({ row: { original: project } }) => <span className="font-medium">{project.name}</span>,
    },
    {
      id: "graph",
      header: () => sortHeader("Graph", "graph", sort, setSort),
      cell: ({ row: { original: project } }) => {
        const index = project.code_index;
        return (
          <div className="flex flex-col items-start gap-1">
            <Badge variant={graphStatus(project) === "failed" ? "destructive" : "secondary"}>
              {active(project) && <Spinner />}
              {graphStatus(project)}
            </Badge>
            {project.processing?.error && <span className="text-xs text-destructive">{project.processing.error}</span>}
            {index && index.parse_error_files.length > 0 && (
              <span className="text-xs text-muted-foreground">
                {index.parse_error_files.length} parse error{index.parse_error_files.length === 1 ? "" : "s"}
              </span>
            )}
          </div>
        );
      },
    },
    { id: "files", header: "Files", cell: ({ row: { original: p } }) => p.code_index?.stats.fileCount ?? "—" },
    { id: "symbols", header: "Symbols", cell: ({ row: { original: p } }) => p.code_index?.stats.nodeCount ?? "—" },
    {
      id: "relationships",
      header: "Relationships",
      cell: ({ row: { original: p } }) => p.code_index?.stats.edgeCount ?? "—",
    },
    {
      id: "languages",
      header: "Languages",
      cell: ({ row: { original: p } }) => (
        <span className="max-w-48">
          {p.code_index ? Object.keys(p.code_index.stats.filesByLanguage).join(", ") : "—"}
        </span>
      ),
    },
    {
      id: "generated",
      header: () => sortHeader("Last generated", "generated", sort, setSort),
      cell: ({ row: { original: p } }) => (p.code_index ? new Date(p.code_index.processed_at).toLocaleString() : "—"),
    },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row: { original: project } }) => {
        const index = project.code_index;
        return (
          <div className="flex items-center justify-end gap-1">
            {index ? (
              <Button
                variant="ghost"
                size="sm"
                aria-label={`View graph for ${project.name}`}
                onClick={() => select(project.id)}
              >
                <Network data-icon="inline-start" />
                View graph
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Generate graph for ${project.name}`}
                disabled={busy || active(project)}
                onClick={() => void generate(project)}
              >
                {active(project) ? <Spinner data-icon="inline-start" /> : <RefreshCw data-icon="inline-start" />}
                Generate graph
              </Button>
            )}
            <RowMenu
              name={project.name}
              items={[
                index && {
                  label: "Regenerate graph",
                  icon: RefreshCw,
                  disabled: busy || active(project),
                  hint: active(project) ? "Generating…" : undefined,
                  onSelect: () => void generate(project),
                },
              ]}
            />
          </div>
        );
      },
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
        data={shown}
        loading={projects === null && !error}
        rowId={(project) => project.id}
        search={{ plural: "code graphs", value: search, onChange: setSearch }}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        toolbar={
          <Button variant="outline" className="sm:ml-auto" disabled={busy} onClick={() => setRefresh((n) => n + 1)}>
            <RefreshCw data-icon="inline-start" />
            Refresh
          </Button>
        }
        empty={
          projects?.length === 0 ? (
            <TableEmpty
              icon={Network}
              title="No code projects yet"
              description="Add a code project, then generate its graph here."
            >
              <Button asChild variant="outline">
                <Link to="/code-projects">Code projects</Link>
              </Button>
            </TableEmpty>
          ) : projects ? (
            "No code graphs match."
          ) : (
            "Could not load code projects."
          )
        }
      />
      {open && (
        <Card>
          <CardHeader>
            <CardTitle>{open.name} · Code graph</CardTitle>
            <Button type="button" variant="outline" className="self-start" onClick={() => select(null)}>
              Close graph
            </Button>
          </CardHeader>
          <CardContent>
            <CodeGraphExplorer projectId={open.id} sourceHash={open.code_index!.source_hash} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
