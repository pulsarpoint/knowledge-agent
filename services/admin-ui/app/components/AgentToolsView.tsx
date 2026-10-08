import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router';
import { ApiError } from '../lib/api';
import { toast } from 'sonner';
import { CLAUDE_CODE_TOOLS_NOTE, TAB_LABELS, TOOL_GROUPS, getAgentTools, saveAgentTools, type AgentTool, type AgentTools } from '../lib/tools';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Skeleton } from '~/components/ui/skeleton';
import { Switch } from '~/components/ui/switch';
import { Textarea } from '~/components/ui/textarea';

function ToolRow({ tool, editing, disabled, onEdit, onChange }: { tool: AgentTool; editing: boolean; disabled: boolean; onEdit: () => void; onChange: (patch: Partial<AgentTool>) => void }) {
  const id = useId();
  return <div data-tool={tool.tool} className={`flex flex-col gap-3 p-3 ${tool.available ? '' : 'opacity-60'}`}>
    <div className="flex items-start gap-3">
      <Switch id={id} aria-label={tool.label} disabled={disabled} checked={tool.mode === 'on'} onCheckedChange={on => onChange({ mode: on ? 'on' : 'off' })} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2"><label htmlFor={id} className="text-sm font-medium">{tool.label}</label>
          {tool.group !== 'mcp' && <code className="text-xs text-muted-foreground">{tool.tool}</code>}
          {tool.usage !== null && <Badge variant="outline">Edited</Badge>}</div>
        {tool.summary && <p className="text-sm text-muted-foreground">{tool.summary}</p>}
        {!tool.available && tool.reason && <p className="text-sm text-muted-foreground">{tool.reason}{tool.tab && <> · <Link className="underline underline-offset-4" to={`?view=${tool.tab}`}>Open {TAB_LABELS[tool.tab] ?? tool.tab}</Link></>}</p>}
      </div>
      <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={onEdit}>{editing ? 'Close' : 'Edit usage'}</Button>
    </div>
    {editing && <Field>
      <FieldLabel htmlFor={`${id}-usage`}>Usage for {tool.label}</FieldLabel>
      <Textarea id={`${id}-usage`} rows={8} maxLength={8000} disabled={disabled} value={tool.usage ?? tool.default_usage}
        onChange={event => onChange({ usage: event.target.value === tool.default_usage ? null : event.target.value })} />
      <FieldDescription>{tool.group === 'mcp' ? 'Added to the prompt under Tool notes; the server keeps its own description.' : 'Replaces the built-in description the model reads.'}</FieldDescription>
      <Button type="button" variant="outline" size="sm" className="w-fit" disabled={disabled || tool.usage === null} onClick={() => onChange({ usage: null })}>Reset</Button>
    </Field>}
  </div>;
}

/** `revision` is the agent's current revision as the page knows it: a newer one (a General save) reloads unchanged tools. */
export default function AgentToolsView({ agentId, revision, onSaved, onDirtyChange }: { agentId: string; revision?: number; onSaved?: (revision: number) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [saved, setSaved] = useState<AgentTools>();
  const [draft, setDraft] = useState<AgentTool[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => {
    let live = true;
    getAgentTools(agentId).then(value => { if (live) { setSaved(value); setDraft(value.tools); setDirty(false); setEditing(null); setStale(false); setError(''); } })
      .catch(reason => { if (live) setError(reason instanceof Error ? reason.message : 'Could not load the tools.'); });
    return () => { live = false; };
  }, [agentId, reload]);
  useEffect(() => { if (saved && revision !== undefined && revision > saved.revision && !dirty && !busy) setReload(n => n + 1); }, [revision]);  // eslint-disable-line react-hooks/exhaustive-deps
  function change(name: string, patch: Partial<AgentTool>) { setDraft(current => current.map(item => item.tool === name ? { ...item, ...patch } : item)); setDirty(true); }
  async function save() {
    if (!saved) return;
    setBusy(true); setError('');
    try {
      const next = await saveAgentTools(agentId, saved.revision, draft);
      setSaved(next); setDraft(next.tools); setDirty(false); setEditing(null); onSaved?.(next.revision);
      toast.success('Tools saved.', { id: 'agent-tools-saved' });
    } catch (reason) { setStale(reason instanceof ApiError && reason.status === 409); setError(reason instanceof Error ? reason.message : 'Could not save the tools.'); }
    finally { setBusy(false); }
  }
  if (!saved) return error ? <Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" size="sm" onClick={() => setReload(n => n + 1)}>Retry</Button></AlertDescription></Alert> : <Skeleton className="h-64 w-full" />;
  if (saved.runtime === 'claude_code') return <Card><CardHeader><CardTitle>Tools</CardTitle><CardDescription>{CLAUDE_CODE_TOOLS_NOTE}</CardDescription></CardHeader>
    <CardContent>{saved.claude_code_tools?.length ? <ul aria-label="Claude Code tools" className="flex flex-wrap gap-2">{saved.claude_code_tools.map(name => <li key={name}><Badge variant="secondary">{name}</Badge></li>)}</ul>
      : <p className="text-sm text-muted-foreground">No Claude Code tools are on.</p>}</CardContent></Card>;
  return <Card><CardHeader><CardTitle>Tools</CardTitle><CardDescription>What this agent can do. The usage instructions are what the model reads about each tool; edit them to steer when and how it is used.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-6">
      {error && <Alert variant="destructive"><AlertDescription>{error}{stale && <Button variant="outline" size="sm" onClick={() => setReload(n => n + 1)}>Reload tools</Button>}</AlertDescription></Alert>}
      {TOOL_GROUPS.map(([group, label]) => {
        const items = draft.filter(item => item.group === group);
        return items.length ? <FieldSet key={group}><FieldLegend>{label}</FieldLegend><div className="flex flex-col divide-y rounded-md border">
          {items.map(item => <ToolRow key={item.tool} tool={item} disabled={busy} editing={editing === item.tool}
            onEdit={() => setEditing(current => current === item.tool ? null : item.tool)} onChange={patch => change(item.tool, patch)} />)}
        </div></FieldSet> : null;
      })}
    </CardContent>
    <CardFooter className="flex-wrap gap-2"><Button disabled={busy || !dirty} onClick={() => void save()}>Save tools</Button>
      <Button variant="outline" disabled={busy || !dirty} onClick={() => { setDraft(saved.tools); setDirty(false); setEditing(null); }}>Discard changes</Button></CardFooter>
  </Card>;
}
