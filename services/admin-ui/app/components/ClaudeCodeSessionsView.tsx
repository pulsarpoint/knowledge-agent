import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { formatTokens, getSessions, statusLabels, type SessionSummary } from '~/lib/claudeCode';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

/** Docs → Generate documents: agent sessions that write documents, newest first; leaving a session page does not stop it. */
export default function ClaudeCodeSessionsView() {
  const [rows, setRows] = useState<SessionSummary[] | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    getSessions(offset, controller.signal).then(result => { if (!controller.signal.aborted) setRows(result.sessions); })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load generations'); });
    return () => controller.abort();
  }, [offset]);
  return <div className="flex flex-col gap-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-muted-foreground">An agent documents the code, environments, services and servers you choose. Follow it live, send messages, then save the draft as a document. Leaving the page does not stop it; runs idle for 24 hours are closed and their history is kept.</p>
      <Button asChild><Link to="/documentation/generations/new">New</Link></Button></div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {rows === null ? <Skeleton className="h-40" /> : <Table><TableHeader><TableRow><TableHead>Generation</TableHead><TableHead>Status</TableHead><TableHead>Agent</TableHead>
      <TableHead>Tokens</TableHead><TableHead>Document</TableHead><TableHead>Last activity</TableHead></TableRow></TableHeader><TableBody>
      {rows.map(row => <TableRow key={row.id}>
        <TableCell><Link className="underline" to={`/documentation/generations/${row.id}`}>{row.title}</Link><p className="text-xs text-muted-foreground">{row.folder}/</p></TableCell>
        <TableCell><Badge variant={row.status === 'failed' ? 'destructive' : 'secondary'}>{statusLabels[row.status]}</Badge></TableCell>
        <TableCell><span>{row.agent_name}</span><p className="text-xs text-muted-foreground">{row.model_name}</p></TableCell>
        <TableCell>{`${formatTokens(row.input_tokens)} in · ${formatTokens(row.output_tokens)} out`}</TableCell>
        <TableCell>{row.document_id ? <Link className="underline" to={`/documents/${row.document_id}`}>{row.document_title}</Link> : 'Not saved'}</TableCell>
        <TableCell>{new Date(row.last_activity_at).toLocaleString()}</TableCell>
      </TableRow>)}
    </TableBody></Table>}
    {rows?.length === 0 && <p>Nothing generated yet. Start with New.</p>}
    {(offset > 0 || rows?.length === 50) && <div className="flex gap-2"><Button variant="outline" disabled={!offset} onClick={() => setOffset(n => n - 50)}>Previous</Button>
      <Button variant="outline" disabled={(rows?.length ?? 0) < 50} onClick={() => setOffset(n => n + 50)}>Next</Button></div>}
  </div>;
}
