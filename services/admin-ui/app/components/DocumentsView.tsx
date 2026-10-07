import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { FileText, Plus, RefreshCw } from 'lucide-react';
import { getDocuments, getDocumentResources, type DocumentResources, type DocumentSummary } from '~/lib/documents';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription } from '~/components/ui/empty';
import { Field, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

export default function DocumentsView() {
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<DocumentSummary[]>([]);
  const [resources, setResources] = useState<DocumentResources | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const offset = Number(params.get('offset') || 0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    Promise.all([getDocuments(params, controller.signal), getDocumentResources(controller.signal)]).then(([data, assets]) => {
      if (!controller.signal.aborted) { setRows(data.documents); setTotal(data.total); setResources(assets); }
    }).catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load documents'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [params, refresh]);
  function filter(key: string, value: string) {
    setParams((old) => { const next = new URLSearchParams(old); value && value !== 'all' ? next.set(key, value) : next.delete(key); if (key !== 'offset') next.delete('offset'); return next; }, { replace: true });
  }
  return <div className="flex flex-col gap-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">{total} documents</p><div className="flex gap-2">
      <Button variant="outline" disabled={loading} onClick={() => setRefresh((n) => n + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
      <Button asChild variant="outline"><Link to="/documentation/generations">Generate documents</Link></Button>
      <Button asChild><Link to="/documents/new"><Plus data-icon="inline-start" />Create document</Link></Button>
    </div></div>
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Field><FieldLabel htmlFor="document-query">Search documents</FieldLabel><Input id="document-query" placeholder="Title, slug or description" value={params.get('q') || ''} onChange={(e) => filter('q', e.target.value)} /></Field>
      {(['code_projects', 'services', 'servers', 'environments'] as const).map((kind) => {
        const key = { code_projects: 'code_project_id', services: 'service_id', servers: 'server_id', environments: 'environment_id' }[kind];
        const label = { code_projects: 'Code project', services: 'Service', servers: 'Server', environments: 'Environment' }[kind];
        return <Field key={kind}><FieldLabel htmlFor={`filter-${kind}`}>{label}</FieldLabel>
          <Select value={params.get(key) || 'all'} onValueChange={(v) => filter(key, v)}><SelectTrigger id={`filter-${kind}`}><SelectValue /></SelectTrigger><SelectContent><SelectGroup>
            <SelectItem value="all">All {label.toLowerCase()}s</SelectItem>{resources?.[kind]?.map((item) => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}
          </SelectGroup></SelectContent></Select></Field>;
      })}
    </div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {loading ? <Skeleton className="h-48 w-full" /> : !error && (rows.length === 0 ? <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><FileText /></EmptyMedia>
      <EmptyTitle>{total === 0 && params.size === 0 ? 'No documents yet' : 'No matching documents'}</EmptyTitle><EmptyDescription>Create a Markdown document and connect it to the projects, services, and servers it describes.</EmptyDescription></EmptyHeader></Empty> : <>
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Document</TableHead><TableHead>Purpose / audience</TableHead><TableHead>Version</TableHead><TableHead>Updated</TableHead></TableRow></TableHeader><TableBody>
        {rows.map((doc) => <TableRow key={doc.id}><TableCell className="max-w-md whitespace-normal"><Link className="font-medium underline-offset-4 hover:underline" to={`/documents/${doc.id}`}>{doc.title}</Link><p className="mt-1 text-sm text-muted-foreground">{doc.description}</p>
          <p className="mt-1 text-xs text-muted-foreground">{doc.code_project_ids.length} code projects · {doc.service_ids.length} services · {doc.server_ids.length} servers</p></TableCell>
          <TableCell><p>{doc.purpose}</p><span className="text-sm text-muted-foreground">{doc.audience === 'both' ? 'People and agents' : doc.audience === 'people' ? 'People' : 'Agents'}</span></TableCell>
          <TableCell><Badge variant="secondary">v{doc.version}</Badge></TableCell><TableCell>{new Date(doc.updated_at).toLocaleString()}</TableCell></TableRow>)}
      </TableBody></Table></div>
    </>)}
    {total > 50 && <div className="flex items-center justify-between gap-3"><span className="text-sm text-muted-foreground">{offset + 1}–{Math.min(offset + 50, total)} of {total}</span><div className="flex gap-2">
      <Button variant="outline" disabled={loading || offset === 0} onClick={() => filter('offset', String(Math.max(0, offset - 50)))}>Previous</Button><Button variant="outline" disabled={loading || offset + 50 >= total} onClick={() => filter('offset', String(offset + 50))}>Next</Button>
    </div></div>}
  </div>;
}
