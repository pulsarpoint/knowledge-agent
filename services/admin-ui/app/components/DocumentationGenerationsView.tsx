import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import DocumentationGenerationForm from './DocumentationGenerationForm';
import { getDocumentationGenerations, type DocumentationGeneration } from '~/lib/documents';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription } from '~/components/ui/empty';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

export default function DocumentationGenerationsView() {
  const [rows, setRows] = useState<DocumentationGeneration[] | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setError('');
    getDocumentationGenerations(offset, controller.signal).then((data) => { if (!controller.signal.aborted) setRows(data.generations); })
      .catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load generation history'); });
    return () => controller.abort();
  }, [offset, refresh]);
  useEffect(() => {
    if (!rows?.some(row => row.status === 'queued' || row.status === 'running')) return;
    const timer = window.setInterval(() => setRefresh(n => n + 1), 5000);
    return () => window.clearInterval(timer);
  }, [rows]);
  return <div className="flex flex-col gap-6"><DocumentationGenerationForm onQueued={() => { setOffset(0); setRefresh(n => n + 1); }} /><div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Generation history</h2><Button variant="outline" onClick={() => setRefresh(n => n + 1)}>Refresh</Button></div>{error ? <Alert variant="destructive"><AlertDescription>{error}<Button variant="link" onClick={() => setRefresh((n) => n + 1)}>Retry</Button></AlertDescription></Alert> : rows === null ? <Skeleton className="h-48 w-full" /> : rows.length === 0 ?
    <Empty className="border"><EmptyHeader><EmptyTitle>No documentation generations yet</EmptyTitle><EmptyDescription>Generated documents and failures will appear here. Queued jobs continue when you leave this page.</EmptyDescription></EmptyHeader></Empty> :
    <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Request</TableHead><TableHead>Status</TableHead><TableHead>Documents</TableHead><TableHead>Started</TableHead></TableRow></TableHeader><TableBody>{rows.map((row) => <TableRow key={row.job_id}><TableCell className="max-w-md whitespace-pre-wrap">{row.instructions}<p className="text-xs text-muted-foreground">{row.mode}</p></TableCell><TableCell><Badge variant="secondary">{row.status}</Badge>{row.status === "running" && row.progress && <p className="text-sm text-muted-foreground">{row.progress}</p>}{row.error && <p className="text-sm text-destructive">{row.error}</p>}</TableCell><TableCell>{row.document_count}{row.result?.export_error && <p className="text-sm text-destructive">{row.result.export_error}</p>}{row.result?.output_folders?.map(folder => <p key={folder} className="max-w-sm break-all text-xs text-muted-foreground">{folder}</p>)}{row.result?.documents?.map((doc, index) => <Link key={doc.id} className="block text-sm underline" to={`/documents/${doc.id}`}>Open document {index + 1} · v{doc.revision}</Link>)}</TableCell><TableCell>{new Date(row.created_at).toLocaleString()}</TableCell></TableRow>)}</TableBody></Table></div>}
    {(offset > 0 || rows?.length === 50) && <div className="flex gap-2"><Button variant="outline" disabled={offset === 0 || rows === null} onClick={() => setOffset(offset - 50)}>Previous</Button><Button variant="outline" disabled={rows === null || rows.length < 50} onClick={() => setOffset(offset + 50)}>Next</Button></div>}
  </div>;
}
