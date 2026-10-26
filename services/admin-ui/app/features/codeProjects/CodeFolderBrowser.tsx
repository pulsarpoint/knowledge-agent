import { useEffect, useState } from "react";
import { FileText, Folder, X } from "lucide-react";
import { getCodeDirectory, getCodeFile, type CodeDirectory } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "~/components/ui/empty";
import { Spinner } from "~/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const errorMessage = (reason: unknown) =>
  reason instanceof Error ? reason.message : "Could not reach administration.";

/** Code projects → Code folder: browse the code folder mounted into Docker (folders, and a file's preview; symbolic
 * links are listed but not followed). */
export default function CodeFolderBrowser() {
  const [path, setPath] = useState("");
  const [offset, setOffset] = useState(0);
  const [directory, setDirectory] = useState<CodeDirectory | null>(null);
  const [directoryError, setDirectoryError] = useState("");
  const [filePath, setFilePath] = useState<string | null>(null);
  const [file, setFile] = useState<{ path: string; text: string } | null>(null);
  const [fileError, setFileError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setDirectory(null);
    setDirectoryError("");
    getCodeDirectory(path, offset, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDirectory(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setDirectoryError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [path, offset]);
  useEffect(() => {
    const controller = new AbortController();
    setFile(null);
    setFileError("");
    if (filePath !== null)
      getCodeFile(filePath, controller.signal)
        .then((value) => {
          if (!controller.signal.aborted) setFile(value);
        })
        .catch((reason) => {
          if (!controller.signal.aborted) setFileError(errorMessage(reason));
        });
    return () => controller.abort();
  }, [filePath]);

  function browse(folder: string) {
    setPath(folder);
    setOffset(0);
    setFilePath(null);
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <Card id="code-browser" className="min-w-0">
        <CardHeader>
          <CardTitle>Code folder</CardTitle>
          <CardDescription>
            {directory ? (
              <>
                <span className="break-all">
                  {directory.host_path ?? "CODE_FOLDER"} → {directory.root}
                </span>
                <br />
                All project folders live inside this mount.
              </>
            ) : (
              "Browse the code folder mounted into Docker."
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex min-w-0 flex-col gap-4">
          <nav aria-label="Code folder path" className="flex flex-wrap items-center gap-1">
            <Button variant="link" onClick={() => browse("")}>
              Code root
            </Button>
            {path
              .split("/")
              .filter(Boolean)
              .map((part, index) => (
                <span key={index} className="flex items-center gap-1">
                  /
                  <Button
                    variant="link"
                    onClick={() =>
                      browse(
                        path
                          .split("/")
                          .slice(0, index + 1)
                          .join("/"),
                      )
                    }
                  >
                    {part}
                  </Button>
                </span>
              ))}
          </nav>

          {directoryError ? (
            <Alert variant="destructive">
              <AlertDescription>{directoryError}</AlertDescription>
            </Alert>
          ) : !directory ? (
            <Spinner />
          ) : (
            <>
              {directory.entries.length === 0 ? (
                <Empty>
                  <EmptyHeader>
                    <EmptyTitle>This folder is empty</EmptyTitle>
                    <EmptyDescription>
                      Add files on the host or clone a Git project into the code root.
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Type</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {directory.entries.map((entry) => (
                      <TableRow key={entry.path}>
                        <TableCell className="max-w-lg whitespace-normal break-all">
                          <Button
                            variant="link"
                            className="max-w-full whitespace-normal"
                            disabled={!["folder", "file"].includes(entry.kind)}
                            onClick={() => (entry.kind === "folder" ? browse(entry.path) : setFilePath(entry.path))}
                          >
                            {entry.kind === "folder" ? <Folder /> : <FileText />}
                            {entry.name}
                          </Button>
                        </TableCell>
                        <TableCell>{entry.kind === "symlink" ? "Symbolic link" : entry.kind}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-muted-foreground">
                  {directory.total} entries · Symbolic links are listed but not followed.
                </p>
                <div className="flex gap-2">
                  {offset > 0 && (
                    <Button variant="outline" onClick={() => setOffset(Math.max(0, offset - 200))}>
                      Previous
                    </Button>
                  )}
                  {directory.next_offset !== null && (
                    <Button variant="outline" onClick={() => setOffset(directory.next_offset!)}>
                      Next
                    </Button>
                  )}
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
      {filePath !== null && (
        <Card className="min-w-0">
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="break-all">{filePath}</CardTitle>
              <Button variant="ghost" size="icon" aria-label="Close file preview" onClick={() => setFilePath(null)}>
                <X />
              </Button>
            </div>
            <CardDescription>File preview</CardDescription>
          </CardHeader>
          <CardContent>
            {fileError ? (
              <Alert variant="destructive">
                <AlertDescription>{fileError}</AlertDescription>
              </Alert>
            ) : !file ? (
              <Spinner />
            ) : (
              <pre
                aria-label="Code file content"
                tabIndex={0}
                className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-4 text-xs"
              >
                {file.text}
              </pre>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
