import { useEffect, useId, useRef, useState } from 'react';
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
  const [text, setText] = useState(tool.usage ?? tool.default_usage);
  useEffect(() => { if (editing) setText(tool.usage ?? tool.default_usage); }, [editing]);  // eslint-disable-line react-hooks/exhaustive-deps
  /** Empty or the built-in text saves as null: the default. */
  function type(value: string) { setText(value); onChange({ usage: !value.trim() || value === tool.default_usage ? null : value }); }
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
      <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={onEdit} aria-expanded={editing} aria-controls={`${id}-panel`}
        aria-label={`${editing ? 'Close usage' : 'Edit usage'} for ${tool.label}`}>{editing ? 'Close' : 'Edit usage'}</Button>
    </div>
    <div id={`${id}-panel`} hidden={!editing}>{editing && <Field>
      <FieldLabel htmlFor={`${id}-usage`}>Usage for {tool.label}</FieldLabel>
      <Textarea id={`${id}-usage`} rows={8} maxLength={8000} disabled={disabled} value={text} onChange={event => type(event.target.value)} />
      <FieldDescription>{tool.group === 'mcp' ? 'Added to the prompt under Tool notes; the server keeps its own description.' : 'Replaces the built-in description the model reads.'} Empty restores the default text.</FieldDescription>
      <Button type="button" variant="outline" size="sm" className="w-fit" disabled={disabled || tool.usage === null} aria-label={`Reset usage for ${tool.label}`}
        onClick={() => { setText(tool.default_usage); onChange({ usage: null }); }}>Reset</Button>
    </Field>}</div>
  </div>;
}

/** Fresh rows from the server with the choices (mode, usage) the user touched in the draft put back on top; untouched
 * rows take the server's, so defaults a General save switched on are not undone by a stale 'off'. */
export function keepChoices(fresh: AgentTool[], draft: AgentTool[], touched: ReadonlySet<string>): AgentTool[] {
  const mine = new Map(draft.filter(item => touched.has(item.tool)).map(item => [item.tool, item]));
  return fresh.map(item => { const own = mine.get(item.tool); return own ? { ...item, mode: own.mode, usage: own.usage } : item; });
}

/** `revision` is the agent's revision as the page knows it: a newer one (a General save) refreshes the tools and
 * keeps unsaved choices. */
export default function AgentToolsView({ agentId, revision, onSaved, onDirtyChange }: { agentId: string; revision?: number; onSaved?: (revision: number) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [saved, setSaved] = useState<AgentTools>();
  const [draft, setDraft] = useState<AgentTool[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const latest = useRef({ draft, dirty });
  latest.current = { draft, dirty };
  const request = useRef(0);
  const touched = useRef(new Set<string>());  // tools whose mode or usage the user changed since the last save or reload
  const requested = useRef<number | undefined>(undefined);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  /** Loads the tools; `discard` drops the draft, otherwise unsaved choices stay on the fresh rows. */
  async function load(discard: boolean) {
    const ticket = ++request.current;
    try {
      const value = await getAgentTools(agentId);
      if (ticket !== request.current) return;
      setSaved(value);
      if (discard || !latest.current.dirty) { setDraft(value.tools); setDirty(false); setEditing(null); touched.current = new Set(); }
      else setDraft(keepChoices(value.tools, latest.current.draft, touched.current));
      if (discard) { setError(''); setStale(false); }
    } catch (reason) { if (ticket === request.current) setError(reason instanceof Error ? reason.message : 'Could not load the tools.'); }
  }
  useEffect(() => { void load(true); return () => { request.current++; }; }, [agentId]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!saved || busy || revision === undefined || revision <= saved.revision || requested.current === revision) return;
    requested.current = revision;
    void load(false);
  }, [revision, saved?.revision, busy]);  // eslint-disable-line react-hooks/exhaustive-deps
  function change(name: string, patch: Partial<AgentTool>) {
    touched.current.add(name);
    setDraft(current => current.map(item => item.tool === name ? { ...item, ...patch } : item)); setDirty(true); setError(''); setStale(false);
  }
  async function save() {
    if (!saved) return;
    setBusy(true); setError('');
    try {
      const next = await saveAgentTools(agentId, saved.revision, draft);
      setSaved(next); setDraft(next.tools); setDirty(false); setEditing(null); setStale(false); touched.current = new Set();
      onSaved?.(next.revision);
      toast.success('Tools saved.', { id: 'agent-tools-saved' });
    } catch (reason) {
      const conflict = reason instanceof ApiError && reason.status === 409;
      setStale(conflict); setError(reason instanceof Error ? reason.message : 'Could not save the tools.');
      if (conflict) await load(false);
    } finally { setBusy(false); }
  }
  if (!saved) return error ? <Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" size="sm" onClick={() => void load(true)}>Retry</Button></AlertDescription></Alert> : <Skeleton className="h-64 w-full" />;
  if (saved.runtime === 'claude_code') return <Card><CardHeader><CardTitle>Tools</CardTitle><CardDescription>{CLAUDE_CODE_TOOLS_NOTE}</CardDescription></CardHeader>
    <CardContent>{saved.claude_code_tools?.length ? <ul aria-label="Claude Code tools" className="flex flex-wrap gap-2">{saved.claude_code_tools.map(name => <li key={name}><Badge variant="secondary">{name}</Badge></li>)}</ul>
      : <p className="text-sm text-muted-foreground">No Claude Code tools are on.</p>}</CardContent></Card>;
  return <Card><CardHeader><CardTitle>Tools</CardTitle><CardDescription>What this agent can do. The usage instructions are what the model reads about each tool; edit them to steer when and how it is used.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-6">
      {error && <Alert variant="destructive"><AlertDescription>{error}{stale && <> Your changes are kept on the current tools: save again to apply them, or reload to discard them.
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void load(true)}>Reload tools</Button></>}</AlertDescription></Alert>}
      {TOOL_GROUPS.map(([group, label]) => {
        const items = draft.filter(item => item.group === group);
        return items.length ? <FieldSet key={group}><FieldLegend>{label}</FieldLegend><div className="flex flex-col divide-y rounded-md border">
          {items.map(item => <ToolRow key={item.tool} tool={item} disabled={busy} editing={editing === item.tool}
            onEdit={() => setEditing(current => current === item.tool ? null : item.tool)} onChange={patch => change(item.tool, patch)} />)}
        </div></FieldSet> : null;
      })}
    </CardContent>
    <CardFooter className="flex-wrap gap-2"><Button disabled={busy || !dirty} onClick={() => void save()}>Save tools</Button>
      <Button variant="outline" disabled={busy || !dirty} onClick={() => { setDraft(saved.tools); setDirty(false); setEditing(null); setError(''); setStale(false); }}>Discard changes</Button></CardFooter>
  </Card>;
}
