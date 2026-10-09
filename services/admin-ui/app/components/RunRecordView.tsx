import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import { getRunRecord, toolUsageLine, type RunEvent, type RunRecord } from '../lib/runs';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Skeleton } from '~/components/ui/skeleton';

const text = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const LABELS: Record<string, string> = { instructions: 'Instructions', input: 'Input', reasoning: 'Reasoning', message: 'Message',
  tool_call: 'Tool call', tool_result: 'Tool result', usage: 'Usage', error: 'Error' };

function Block({ children }: { children: string }) {
  return <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 text-xs">{children}</pre>;
}

function Event({ event }: { event: RunEvent }) {
  const p = event.payload;
  const who = typeof p.agent === 'string' ? p.agent : event.agent_id;
  const head = <span className="text-sm font-medium">{LABELS[event.kind] ?? event.kind}{event.kind.startsWith('tool') && p.name ? ` · ${p.name}` : ''} <span className="font-normal text-muted-foreground">· {who}</span></span>;
  if (event.kind === 'instructions' || event.kind === 'input')
    return <details><summary className="cursor-pointer">{head}</summary><Block>{event.kind === 'input' ? text({ task: p.task, history: p.history }) : text(p.text)}</Block></details>;
  if (event.kind === 'reasoning')
    return <details><summary className="cursor-pointer">{head}</summary>{p.available === false ? <p className="text-sm text-muted-foreground">Reasoning not returned by the provider.</p> : <Block>{text(p.text)}</Block>}</details>;
  if (event.kind === 'tool_call') return <div>{head}<Block>{text(p.arguments)}</Block></div>;
  if (event.kind === 'tool_result') return <div>{head}<Block>{text(p.output)}</Block></div>;
  if (event.kind === 'message') return <div>{head}<Block>{text(p.text)}</Block></div>;
  if (event.kind === 'usage') return <p className="text-xs text-muted-foreground">{who}: {String(p.input_tokens)} in · {String(p.output_tokens)} out · {String(p.total_tokens)} total</p>;
  if (event.kind === 'error') return <Alert variant="destructive"><AlertDescription>{String(p.type)}: {String(p.message)}</AlertDescription></Alert>;
  return null;
}

export default function RunRecordView({ runKey }: { runKey: string }) {
  const call = useSearchParams()[0].get('call') ?? undefined;
  const [record, setRecord] = useState<RunRecord | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    getRunRecord(runKey, controller.signal, call).then(data => { if (!controller.signal.aborted) setRecord(data); })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load the run record'); });
    return () => controller.abort();
  }, [runKey, call]);
  if (error) return <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>;
  if (!record) return <Skeleton className="h-64 w-full" />;
  const start = record.events.find(e => e.kind === 'start')?.payload ?? {};
  const end = record.events.filter(e => e.kind === 'end').at(-1)?.payload;
  const tokens = record.events.filter(e => e.kind === 'usage').reduce((sum, e) => sum + Number(e.payload.total_tokens ?? 0), 0);
  return <div className="flex flex-col gap-4">
    <div className="flex flex-col gap-1">
      <h2 className="text-lg font-semibold">{String(start.title ?? record.run_key)}</h2>
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <Badge variant="secondary">{String(start.kind ?? 'run')}</Badge><span>{String(start.agent ?? record.agent_id)}</span>
        <span>· {record.user ?? 'system'}</span><Badge>{String(end?.status ?? 'running')}</Badge><span>{tokens.toLocaleString()} tokens</span>{record.tool_counts && <><span>·</span><span aria-label="Tools used">{toolUsageLine(record.tool_counts)}</span></>}
      </div>
    </div>
    <ol className="flex flex-col gap-3">{record.events.filter(e => !['start', 'end'].includes(e.kind)).map(e => <li key={e.seq}><Event event={e} /></li>)}</ol>
  </div>;
}
