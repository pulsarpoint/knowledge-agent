import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { RefreshCw } from 'lucide-react';
import CodeGraphExplorer from './CodeGraphExplorer';
import { getCodeProjects, processCodeProject, type CodeProject } from '../lib/codeProjects';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '~/components/ui/card';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '~/components/ui/empty';
import { Spinner } from '~/components/ui/spinner';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

const active = (project: CodeProject) => ['queued', 'running'].includes(project.processing?.status ?? '')
  || ['queued', 'running', 'waiting_input'].includes(project.sync_status ?? '');
const graphStatus = (project: CodeProject) => project.processing && ['queued', 'running', 'failed'].includes(project.processing.status)
  ? project.processing.status : project.code_index?.status ?? 'Not generated';
const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : 'Could not reach administration.';

/** Every code project's graph: summary, generation and the explorer. */
export default function CodeGraphsView() {
  const [params, setParams] = useSearchParams();
  const selected = params.get('project');
  const [projects, setProjects] = useState<CodeProject[] | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    getCodeProjects(controller.signal).then(result => setProjects(result.projects))
      .catch(reason => { if (!controller.signal.aborted) setError(errorMessage(reason)); });
    return () => controller.abort();
  }, [refresh]);

  const select = (id: string | null) => setParams(current => {
    const next = new URLSearchParams(current);
    if (id) next.set('project', id); else next.delete('project');
    return next;
  });

  async function generate(project: CodeProject) {
    setBusy(true); setError(''); setNotice('');
    try {
      await processCodeProject(project.id);
      setNotice(`Code graph generation queued for ${project.name}.`);
      setRefresh(n => n + 1);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  }

  const open = projects?.find(project => project.id === selected && project.code_index);
  return <div className="flex flex-col gap-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted-foreground">{projects ? `${projects.length} code projects` : 'Loading…'}</p>
      <Button variant="outline" disabled={busy} onClick={() => setRefresh(n => n + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
    </div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    {projects === null ? <Spinner /> : projects.length === 0
      ? <Empty><EmptyHeader><EmptyTitle>No code projects yet</EmptyTitle><EmptyDescription><Link to="/code-projects">Add a project</Link>, then generate its graph here.</EmptyDescription></EmptyHeader></Empty>
      : <Table><TableHeader><TableRow>
          <TableHead>Project</TableHead><TableHead>Graph</TableHead><TableHead>Files</TableHead><TableHead>Symbols</TableHead>
          <TableHead>Relationships</TableHead><TableHead>Languages</TableHead><TableHead>Last generated</TableHead><TableHead>Actions</TableHead>
        </TableRow></TableHeader><TableBody>{projects.map(project => {
          const index = project.code_index;
          return <TableRow key={project.id}>
            <TableCell className="font-medium">{project.name}</TableCell>
            <TableCell><div className="flex flex-col items-start gap-1">
              <Badge variant={graphStatus(project) === 'failed' ? 'destructive' : 'secondary'}>{active(project) && <Spinner />}{graphStatus(project)}</Badge>
              {project.processing?.error && <span className="text-xs text-destructive">{project.processing.error}</span>}
              {index && index.parse_error_files.length > 0 && <span className="text-xs text-muted-foreground">{index.parse_error_files.length} parse error{index.parse_error_files.length === 1 ? '' : 's'}</span>}
            </div></TableCell>
            <TableCell>{index?.stats.fileCount ?? '—'}</TableCell>
            <TableCell>{index?.stats.nodeCount ?? '—'}</TableCell>
            <TableCell>{index?.stats.edgeCount ?? '—'}</TableCell>
            <TableCell className="max-w-48 whitespace-normal">{index ? Object.keys(index.stats.filesByLanguage).join(', ') : '—'}</TableCell>
            <TableCell>{index ? new Date(index.processed_at).toLocaleString() : '—'}</TableCell>
            <TableCell><div className="flex flex-wrap gap-2">
              {index && <Button variant="outline" size="sm" aria-label={`View graph for ${project.name}`} onClick={() => select(project.id)}>View graph</Button>}
              <Button variant="outline" size="sm" disabled={busy || active(project)} aria-label={`Generate graph for ${project.name}`} onClick={() => void generate(project)}>Generate graph</Button>
            </div></TableCell>
          </TableRow>;
        })}</TableBody></Table>}
    {open && <Card><CardHeader><CardTitle>{open.name} · Code graph</CardTitle>
      <Button type="button" variant="outline" className="self-start" onClick={() => select(null)}>Close graph</Button></CardHeader>
      <CardContent><CodeGraphExplorer projectId={open.id} sourceHash={open.code_index!.source_hash} /></CardContent></Card>}
  </div>;
}
