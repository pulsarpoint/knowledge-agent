import { useEffect, useState } from 'react';
import { getApiAnalysis, type ApiAnalysis } from '~/lib/documents';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/collapsible';
import Markdown from './Markdown';

export default function ApiAnalysisProgress({ jobId, status }: { jobId: string; status: string }) {
  const [analysis, setAnalysis] = useState<ApiAnalysis | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setAnalysis(null); setError('');
    async function load() {
      try {
        const value = await getApiAnalysis(jobId, controller.signal);
        if (controller.signal.aborted) return;
        setAnalysis(value); setError('');
        if (status === 'running' || status === 'queued') timer = setTimeout(load, 3000);
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load API sections'); }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [jobId, status]);
  if (error) return <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>;
  if (!analysis?.inventory) return null;
  const { inventory, sections } = analysis;
  const count = inventory.batches.reduce((n, batch) => n + batch.routes.length, 0);
  return <section aria-label="API route analysis" className="flex min-w-0 flex-col gap-3">
    <p className="font-medium">{count} route registrations · {sections.length}/{inventory.batches.length} sections saved</p>
    <p className="text-sm text-muted-foreground">The graph selects each endpoint and its related source ranges. Each section is written from that evidence in a fresh context. Completed sections from failed runs are reused on retry when source, prompt and model settings match. Overall API completeness still requires checking the coverage gaps.</p>
    {count === 0 && <Alert><AlertDescription>The graph contains no route nodes. This does not prove there are no APIs. Check the graph’s language and framework coverage.</AlertDescription></Alert>}
    <Collapsible><CollapsibleTrigger asChild><Button type="button" variant="outline" size="sm">Inspect API routes and saved sections</Button></CollapsibleTrigger>
      <CollapsibleContent className="flex min-w-0 flex-col gap-3 pt-3">
        {inventory.coverage.map(project => <p key={project.id} className="text-sm text-muted-foreground">{project.name}: {project.route_count} registrations. {project.limitations}</p>)}
        {inventory.batches.map((batch, i) => {
          const section = sections.find(s => s.batch_key === batch.key);
          return <div key={batch.key} className="flex min-w-0 flex-col gap-2 rounded-md border p-3">
            <p className="font-medium">Section {i + 1} · {section ? section.reused_from ? 'Saved · reused' : 'Saved' : 'Not completed'}</p>
            {batch.routes.map(route => <p key={route.id} className="break-words text-sm"><span className="font-mono">{route.name}</span><span className="block text-xs text-muted-foreground">{route.file_path}:{route.start_line}</span></p>)}
            <p className="text-xs text-muted-foreground">{batch.project_name}{batch.source_characters != null && <> · {batch.source_characters.toLocaleString()} source characters</>}</p>
            {batch.gaps.length > 0 && <Collapsible><CollapsibleTrigger asChild><Button type="button" variant="ghost" size="sm">Evidence gaps ({batch.gaps.length})</Button></CollapsibleTrigger><CollapsibleContent><ul className="list-disc space-y-1 pl-5 text-sm">{batch.gaps.map((gap, n) => <li key={n}>{gap}</li>)}</ul></CollapsibleContent></Collapsible>}
            {section && <Collapsible><CollapsibleTrigger asChild><Button type="button" variant="outline" size="sm">Preview section {i + 1}</Button></CollapsibleTrigger><CollapsibleContent className="min-w-0 pt-3"><Markdown content={section.markdown} /></CollapsibleContent></Collapsible>}
          </div>;
        })}
      </CollapsibleContent>
    </Collapsible>
  </section>;
}
