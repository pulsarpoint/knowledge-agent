import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Braces, Plus } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getOutputSchemas, type OutputSchemaSummary } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

/** Agents → Output schemas: the library of JSON Schemas a run can be asked to finish with. The page title comes from
 * AdminLayout. */
export default function OutputSchemasView() {
  const [rows, setRows] = useState<OutputSchemaSummary[] | null>(null);
  const [error, setError] = useState("");
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
  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        <Button asChild>
          <Link to="/output-schemas/new">
            <Plus data-icon="inline-start" />
            New schema
          </Link>
        </Button>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {rows === null && <Skeleton className="h-32 w-full" />}
      {rows?.length === 0 && !error && (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Braces />
            </EmptyMedia>
            <EmptyTitle>Output schemas</EmptyTitle>
            <EmptyDescription>
              No output schemas yet. A run started with a schema (from a handler, the API or an agent's Chat tab)
              finishes with JSON in that shape.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
      {rows && rows.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Version</TableHead>
                <TableHead>Used by handlers</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-medium">
                    <Link className="hover:underline" to={`/output-schemas/${encodeURIComponent(row.id)}`}>
                      {row.name}
                    </Link>
                  </TableCell>
                  <TableCell className="max-w-md whitespace-normal">{row.description}</TableCell>
                  <TableCell>v{row.current_version}</TableCell>
                  <TableCell className="whitespace-normal">{row.used_by.join(", ") || "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
