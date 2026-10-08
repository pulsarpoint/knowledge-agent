import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { getRuns, runPath, topTools, toolUsageLine, type RunSummary } from '../lib/runs';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Empty, EmptyDescription } from '~/components/ui/empty';
import { Input } from '~/components/ui/input';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

const KINDS: Record<string, string> = { test: 'Agent test', test_session: 'Test session', claude_code: 'Claude Code', native_session: 'Native session' };

export default function RunsView() {
  const [params, setParams] = useSearchParams();
  const kind = params.get('kind') ?? '';
  const user = params.get('user') ?? '';
  const keyPrefix = params.get('key_prefix') ?? '';
  const [before, setBefore] = useState<number | undefined>();
  const [rows, setRows] = useState<RunSummary[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    getRuns({ kind, user, key_prefix: keyPrefix, before }, controller.signal)
      .then(data => { if (!controller.signal.aborted) { setRows(data.runs); setNext(data.next); } })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load runs'); });
    return () => controller.abort();
  }, [kind, user, keyPrefix, before]);
  const update = (key: string, value: string) => setParams(current => {
    if (value) current.set(key, value); else current.delete(key);
    return current;
  });
  return <div className="flex flex-col gap-4">
    <div className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-sm">Run type
        <select aria-label="Run type" className="h-9 rounded-md border bg-background px-2" value={kind} onChange={e => { setBefore(undefined); update('kind', e.target.value); }}>
          <option value="">All</option>{Object.entries(KINDS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm">User
        <Input aria-label="User" placeholder="email" defaultValue={user} onBlur={e => { setBefore(undefined); update('user', e.target.value.trim()); }} />
      </label>
      {keyPrefix && <Button variant="outline" size="sm" onClick={() => update('key_prefix', '')}>Clear run filter</Button>}
    </div>
    {error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
      : rows === null ? <Skeleton className="h-40 w-full" />
      : rows.length === 0 ? <Empty className="border"><EmptyDescription>No recorded runs yet.</EmptyDescription></Empty>
      : <div className="rounded-lg border"><Table>
          <TableHeader><TableRow><TableHead>Run</TableHead><TableHead>Type</TableHead><TableHead>Agent</TableHead><TableHead>User</TableHead><TableHead>Status</TableHead><TableHead>Tokens</TableHead><TableHead>Tools</TableHead><TableHead>Started</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(row => <TableRow key={row.run_key}>
            <TableCell className="max-w-md"><Link className="underline underline-offset-4" to={runPath(row.run_key)}>{row.title || row.run_key}</Link></TableCell>
            <TableCell><Badge variant="secondary">{row.kind ? KINDS[row.kind] ?? row.kind : 'run'}</Badge></TableCell>
            <TableCell>{row.agent ?? row.agent_id}</TableCell>
            <TableCell>{row.user ?? '—'}</TableCell>
            <TableCell>{row.status ?? 'running'}</TableCell>
            <TableCell>{row.total_tokens.toLocaleString()}</TableCell>
            <TableCell className="max-w-xs whitespace-normal" title={toolUsageLine(row.tool_counts)}>{topTools(row.tool_counts)}</TableCell>
            <TableCell>{new Date(row.started_at).toLocaleString()}</TableCell>
          </TableRow>)}</TableBody>
        </Table></div>}
    {next !== null && <Button variant="outline" className="self-start" onClick={() => setBefore(next)}>Older runs</Button>}
  </div>;
}
