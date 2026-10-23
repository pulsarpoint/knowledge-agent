import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getDocumentationFolder, getDocumentationFolders, type DocumentationFolders, type FolderDocument } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const folderLink = (folder: string) => `/documentation/folders/${folder.split("/").map(encodeURIComponent).join("/")}`;
const message = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);
const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "—");

export default function DocumentationFoldersView() {
  const [data, setData] = useState<DocumentationFolders | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getDocumentationFolders(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e, "Could not load documentation folders"));
      });
    return () => controller.abort();
  }, []);
  return (
    <div className="flex flex-col gap-5">
      <p className="text-muted-foreground">The folders your documents are filed in.</p>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {data === null ? (
        !error && <Skeleton className="h-40" />
      ) : data.folders.length === 0 ? (
        <p>
          No documentation folders yet. Write a code project document into a folder with an agent from Documents → New.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Folder</TableHead>
              <TableHead>Documents</TableHead>
              <TableHead>Code projects</TableHead>
              <TableHead>Last generated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.folders.map((row) => (
              <TableRow key={row.folder}>
                <TableCell>
                  <Link className="underline" to={folderLink(row.folder)}>
                    {row.folder}
                  </Link>
                </TableCell>
                <TableCell>{row.document_count}</TableCell>
                <TableCell>{row.code_projects.join(", ") || "—"}</TableCell>
                <TableCell>{when(row.last_generated_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

export function DocumentationFolderView({ folder }: { folder: string }) {
  const [rows, setRows] = useState<FolderDocument[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setRows(null);
    setError("");
    getDocumentationFolder(folder, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setRows(result.documents);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e, "Could not open the folder"));
      });
    return () => controller.abort();
  }, [folder]);
  return (
    <div className="flex flex-col gap-5">
      <Link className="underline" to="/documentation/folders">
        Documentation folders
      </Link>
      <h1 className="text-2xl font-semibold">{folder}</h1>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {rows === null ? (
        !error && <Skeleton className="h-40" />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Document</TableHead>
              <TableHead>Code project</TableHead>
              <TableHead>Last generated</TableHead>
              <TableHead>Version</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((d) => (
              <TableRow key={d.id}>
                <TableCell>
                  <Link className="underline" to={`/documents/${d.id}`}>
                    {d.title}
                  </Link>
                </TableCell>
                <TableCell>{d.code_projects.join(", ") || "—"}</TableCell>
                <TableCell>{when(d.last_generated_at)}</TableCell>
                <TableCell>v{d.version}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
