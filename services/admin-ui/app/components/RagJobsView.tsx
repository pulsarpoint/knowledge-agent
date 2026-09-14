import { useEffect, useState } from "react";
import { ragRequest, type RagJob } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

export default function RagJobsView() {
  const [jobs, setJobs] = useState<RagJob[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try { const result = await ragRequest<{ jobs: RagJob[] }>("jobs", "GET", undefined, controller.signal);
        if (controller.signal.aborted) return; setJobs(result.jobs); setError("");
        if (result.jobs.some((job) => ["queued", "running"].includes(job.status))) timer = setTimeout(load, 2000);
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load jobs"); }
    }
    load(); return () => { controller.abort(); clearTimeout(timer); };
  }, [reload]);
  async function action(resource: string, body?: unknown) {
    setBusy(true); setError(""); try { await ragRequest(resource, "POST", body); setReload((v) => v + 1); }
    catch (e) { setError(e instanceof Error ? e.message : "Job action failed"); } finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-4">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">Latest 100 runs. Active jobs refresh automatically. Processing failures preserve the previous index; deleting a folder removes its indexed data.</p><Button variant="outline" disabled={busy} onClick={() => setReload((v) => v + 1)}>Refresh jobs</Button></div>
    <Table><TableHeader><TableRow><TableHead>Folder / started</TableHead><TableHead>Status</TableHead><TableHead>Processing</TableHead><TableHead>Actions</TableHead></TableRow></TableHeader><TableBody>
      {jobs.map((job) => <TableRow key={job.id}><TableCell className="align-top"><p className="font-medium">{job.path}{job.input.recursive ? "/**" : ""}</p><p className="text-xs text-muted-foreground">{new Date(job.created_at).toLocaleString()}</p><p className="text-xs text-muted-foreground">{job.input.force ? "Force rebuild" : "Incremental"}</p></TableCell>
        <TableCell className="align-top"><Badge variant={job.status === "failed" ? "destructive" : "secondary"}>{job.status}</Badge></TableCell>
        <TableCell className="max-w-lg align-top"><dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">{Object.entries(job.result).map(([name, count]) => <div key={name} className="flex gap-2"><dt className="text-muted-foreground">{name.replaceAll("_", " ")}</dt><dd>{count}</dd></div>)}</dl>{job.error && <p className="mt-2 text-sm text-destructive">{job.error}</p>}</TableCell>
        <TableCell className="align-top">{job.folder_deleted_at ? <span className="text-xs text-muted-foreground">Folder deleted</span> : ["queued", "running"].includes(job.status) ? <Button variant="outline" size="sm" disabled={busy} onClick={() => action(`jobs/${job.id}/cancel`)}>Cancel</Button> : <Button variant="outline" size="sm" disabled={busy || jobs.some((j) => ["queued", "running"].includes(j.status))} onClick={() => action("jobs", job.input)}>Run again</Button>}</TableCell>
      </TableRow>)}
      {jobs.length === 0 && <TableRow><TableCell colSpan={4} className="py-12 text-center text-muted-foreground">No processing jobs yet. Select a folder on Files &amp; folders to start.</TableCell></TableRow>}
    </TableBody></Table>
  </div>;
}
