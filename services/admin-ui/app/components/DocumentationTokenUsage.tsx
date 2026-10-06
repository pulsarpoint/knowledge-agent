import type { DocumentationGeneration } from '~/lib/documents';
import { Button } from '~/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/collapsible';
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from '~/components/ui/table';

const names: Record<string, string> = { project: 'Code project document', overview: 'Application overview', api: 'API documentation', messaging: 'Messaging documentation', code: 'Code analysis', diagram: 'Code diagram',
  deployment: 'Deployment', deployment_diagram: 'Deployment diagram', architecture: 'Environment architecture', infrastructure: 'Server infrastructure' };

export default function DocumentationTokenUsage({ run, compact = false }: { run: DocumentationGeneration; compact?: boolean }) {
  const rows = run.token_usage ?? [];
  const phases = [...new Set([...(run.prompt_snapshot?.map(p => p.kind) ?? []), ...rows.map(row => row.phase)])];
  const totals = rows.reduce((sum, row) => ({ input: sum.input + row.input_tokens, output: sum.output + row.output_tokens,
    total: sum.total + row.total_tokens, calls: sum.calls + row.requests, reported: sum.reported + row.reported_calls,
    cached: sum.cached + (row.input_tokens_details?.cached_tokens ?? 0),
    cacheWrite: sum.cacheWrite + (row.input_tokens_details?.cache_write_tokens ?? 0),
    reasoning: sum.reasoning + (row.output_tokens_details?.reasoning_tokens ?? 0),
  }), { input: 0, output: 0, total: 0, calls: 0, reported: 0, cached: 0, cacheWrite: 0, reasoning: 0 });
  const available = rows.length > 0 && (totals.reported > 0 || totals.calls === 0);
  const partial = totals.reported < totals.calls;
  const pending = ['queued', 'running'].includes(run.status);
  const number = (value: number) => value.toLocaleString();
  const summary = available ? `${number(totals.input)} in · ${number(totals.output)} out · ${number(totals.total)} total${partial ? ' · partial' : ''}${pending ? ' so far' : ''}` : pending ? 'Usage pending' : 'Usage not recorded';
  const details = <section aria-label="Phase token usage" className="flex min-w-0 flex-col gap-2">
    <Table><TableHeader><TableRow><TableHead>Phase / step</TableHead><TableHead className="text-right">Input</TableHead><TableHead className="text-right">Output</TableHead><TableHead className="text-right">Total</TableHead><TableHead className="text-right">Model calls</TableHead></TableRow></TableHeader>
      <TableBody>{phases.map(phase => {
        const steps = rows.filter(row => row.phase === phase);
        return steps.length ? steps.map(row => {
          const known = row.reported_calls > 0 || row.requests === 0;
          return <TableRow key={`${phase}:${row.step}`}><TableCell>{names[phase] ?? phase}{row.step !== 'generation' && ` · ${row.step === 'inspection' ? 'Source inspection' : 'Writing'}`}{row.reported_calls < row.requests && ' · partial'}</TableCell>
            {[row.input_tokens, row.output_tokens, row.total_tokens].map((value, i) => <TableCell key={i} className="text-right tabular-nums">{known ? number(value) : 'Unavailable'}</TableCell>)}<TableCell className="text-right tabular-nums">{number(row.requests)}</TableCell></TableRow>;
        }) : <TableRow key={phase}><TableCell>{names[phase] ?? phase}</TableCell><TableCell colSpan={4}>{pending || rows.length > 0 ? 'Not started' : 'Not recorded for this run'}</TableCell></TableRow>;
      })}</TableBody>
      {rows.length > 0 && <TableFooter><TableRow><TableCell>{partial ? 'Recorded total · partial' : 'Total'}{pending && ' so far'}</TableCell>
        {[totals.input, totals.output, totals.total].map((value, i) => <TableCell key={i} className="text-right tabular-nums">{available ? number(value) : 'Unavailable'}</TableCell>)}<TableCell className="text-right tabular-nums">{number(totals.calls)}</TableCell></TableRow></TableFooter>}
    </Table>
    {(totals.cached > 0 || totals.cacheWrite > 0 || totals.reasoning > 0) && <p className="text-xs text-muted-foreground">{[
      totals.cached > 0 && `${number(totals.cached)} cached input tokens`,
      totals.cacheWrite > 0 && `${number(totals.cacheWrite)} cache-write tokens`,
      totals.reasoning > 0 && `${number(totals.reasoning)} reasoning tokens`,
    ].filter(Boolean).join(' · ')}. These details are not added again to the total.</p>}
    <p className="text-xs text-muted-foreground">Usage from completed model responses, including repeated context. Updates after each model call, including calls in failed runs.</p>
    {partial && <p className="text-xs text-muted-foreground">Some model responses did not report token usage; the recorded total is incomplete.</p>}
  </section>;
  return compact ? <Collapsible><CollapsibleTrigger asChild><Button type="button" variant="ghost" size="sm">{summary}</Button></CollapsibleTrigger><CollapsibleContent>{details}</CollapsibleContent></Collapsible>
    : <div className="flex min-w-0 flex-col gap-2"><h4 className="font-medium">Token usage · {summary}</h4>{details}</div>;
}
