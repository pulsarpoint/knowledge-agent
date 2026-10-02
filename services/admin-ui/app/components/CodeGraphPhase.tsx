import { getDocumentationWorkflow } from '~/lib/documents';
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { getCodeProjects, processCodeProject, type CodeProject } from '~/lib/codeProjects';
import CodeGraphExplorer from './CodeGraphExplorer';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Field, FieldLabel } from '~/components/ui/field';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';

export default function CodeGraphPhase({ projectIds, onReady, workflowId, beforeRun, onQueued }: { workflowId?: string; beforeRun?: () => Promise<void>; onQueued?: () => void; projectIds: string[]; onReady: (hashes: Record<string, string>) => void }) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<CodeProject[]>([]);
  const [selected, setSelected] = useState(params.get('graph_project') ?? '');
  const [refresh, setRefresh] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const key = [...projectIds].sort().join(',');
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    onReady({});
    async function load() {
      try {
        const [all, workflow] = await Promise.all([getCodeProjects(), workflowId ? getDocumentationWorkflow(workflowId) : Promise.resolve(null)]);
        if (cancelled) return;
        const rows = all.projects.filter(p => key.split(',').includes(p.id)).map(p => {
          if (!workflow) return p;
          const run = workflow.graph_runs.find(r => r.repository_id === p.id);
          return {...p, processing: run ? {status:run.status,error:run.error}:null,
            code_index: run?.status === 'succeeded' && run.result?.source_hash ? run.result as CodeProject['code_index'] : null};
        });
        setProjects(rows);
        setSelected(previous => rows.some(p => p.id === previous) ? previous : rows[0]?.id ?? '');
        const busy = rows.some(p => ['queued', 'running'].includes(p.processing?.status ?? ''));
        onReady(Object.fromEntries(rows.filter(p => p.code_index && !['queued','running','failed'].includes(p.processing?.status ?? '')).map(p => [p.id, p.code_index!.source_hash])));
        if (busy) timer = setTimeout(load, 2000);
      } catch (e) { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load graph status'); }
    }
    if (key) void load(); else setProjects([]);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [key, refresh, onReady, workflowId]);
  const active = projects.find(p => p.id === selected);
  function selectProject(id: string) {
    setSelected(id);
    if (workflowId) {
      const next = new URLSearchParams(params); next.set('graph_project', id);
      navigate({search:next.toString(),hash:'#phase-graph'},{replace:true,preventScrollReset:true});
    }
  }
  const busy = pending || projects.some(p => ['queued','running'].includes(p.processing?.status ?? ''));
  async function generate() {
    setPending(true); setError(''); onReady({});
    try {
      await beforeRun?.();
      for (const id of projectIds) await processCodeProject(id, workflowId);
      onQueued?.();
      setRefresh(value => value + 1);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not generate graph'); }
    finally { setPending(false); }
  }
  return <section id="phase-graph" className="flex min-w-0 flex-col gap-4 rounded-md border p-4" aria-label="Phase 1: Generate code graph">
    <div><h3 className="font-semibold">Phase 1 · Generate and inspect the code graph</h3><p className="text-sm text-muted-foreground">CodeGraph analyzes the selected source without calling an LLM. Review the graph before explicitly continuing to documentation. Unchanged files reuse their index.</p></div>
    <p className="text-sm text-muted-foreground">Token usage: 0 · deterministic analysis, no model calls.</p>
    {projectIds.length ? <>
      <Button type="button" variant="outline" className="self-start" disabled={busy} onClick={() => void generate()}>{busy ? 'Generating graph…' : 'Generate graph'}</Button>
      <div className="flex flex-col gap-2">{projects.map(p => <div key={p.id} className="flex flex-wrap items-center gap-2 text-sm"><span>{p.name}</span><Badge variant={p.processing?.status === 'failed' ? 'destructive' : 'secondary'}>{['queued','running','failed'].includes(p.processing?.status ?? '') ? p.processing!.status : p.code_index?.status ?? 'Not generated'}</Badge>{p.code_index && <span>{p.code_index.stats.nodeCount} symbols · {p.code_index.stats.edgeCount} relationships · {p.code_index.unresolved_references} unresolved</span>}{p.processing?.error && <span className="text-destructive">{p.processing.error}</span>}</div>)}</div>
      {projects.length > 1 && <Field><FieldLabel htmlFor="graph-project">Inspect project graph</FieldLabel><Select value={selected} onValueChange={selectProject}><SelectTrigger id="graph-project"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>{projects.map(p => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>}
      {active?.code_index && <CodeGraphExplorer projectId={active.id} sourceHash={active.code_index.source_hash} />}
    </> : <p className="text-sm text-muted-foreground">No source code is linked to this service. Code documentation will record that code is unavailable.</p>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
  </section>;
}
