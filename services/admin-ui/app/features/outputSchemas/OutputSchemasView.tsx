import { useEffect, useState } from "react";
import { Link } from "react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { Braces, Plus } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getOutputSchemas, type OutputSchemaSummary } from "./api";
import DataTable, { sortHeader, sortRows, TableEmpty, type Sort } from "~/components/DataTable";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";

type SchemaSort = "name" | "version";
const byName = (a: OutputSchemaSummary, b: OutputSchemaSummary) => a.name.localeCompare(b.name);
const SCHEMA_ORDER: Record<SchemaSort, (a: OutputSchemaSummary, b: OutputSchemaSummary) => number> = {
  name: byName,
  version: (a, b) => a.current_version - b.current_version || byName(a, b),
};

/** Agents → Output schemas: the library of JSON Schemas a run can be asked to finish with, in a table with search and
 * sortable headers. The page title comes from AdminLayout. */
export default function OutputSchemasView() {
  const [rows, setRows] = useState<OutputSchemaSummary[] | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort<SchemaSort>>({ key: "name", descending: false });
  useEffect(() => {
    const controller = new AbortController();
    getOutputSchemas(controller.signal)
      .then((result) => setRows(result.schemas))
      .catch((e) => {
        if (!controller.signal.aborted) {
          setRows([]);
          setError(e instanceof ApiError ? e.message : "Could not load the output schemas.");
        }
      });
    return () => controller.abort();
  }, []);
  const text = search.trim().toLowerCase();
  const shown = sortRows(
    (rows ?? []).filter((row) => `${row.name}\n${row.description}`.toLowerCase().includes(text)),
    sort,
    SCHEMA_ORDER,
  );
  const newSchema = (
    <Button asChild>
      <Link to="/output-schemas/new">
        <Plus data-icon="inline-start" />
        New schema
      </Link>
    </Button>
  );
  const columns: ColumnDef<OutputSchemaSummary, unknown>[] = [
    {
      id: "name",
      header: () => sortHeader("Name", "name", sort, setSort),
      cell: ({ row: { original: row } }) => (
        <Link className="font-medium hover:underline" to={`/output-schemas/${encodeURIComponent(row.id)}`}>
          {row.name}
        </Link>
      ),
    },
    {
      id: "description",
      header: "Description",
      cell: ({ row: { original: row } }) => <span className="max-w-md">{row.description}</span>,
    },
    {
      id: "version",
      header: () => sortHeader("Version", "version", sort, setSort),
      cell: ({ row: { original: row } }) => `v${row.current_version}`,
    },
    {
      id: "used",
      header: "Used by handlers",
      cell: ({ row: { original: row } }) => row.used_by.join(", ") || "—",
    },
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">{newSchema}</div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <DataTable
        columns={columns}
        data={shown}
        loading={rows === null}
        rowId={(row) => row.id}
        search={{ plural: "output schemas", value: search, onChange: setSearch }}
        sort={sort}
        resetKey={`${sort.key}\n${sort.descending}`}
        empty={
          rows?.length === 0 ? (
            error ? (
              "Could not load output schemas."
            ) : (
              <TableEmpty
                icon={Braces}
                title="No output schemas yet"
                description="A run started with a schema (from a handler, the API or an agent's Chat tab) finishes with JSON in that shape."
              >
                {newSchema}
              </TableEmpty>
            )
          ) : (
            "No output schemas match."
          )
        }
      />
    </div>
  );
}
