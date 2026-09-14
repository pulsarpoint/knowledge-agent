import { useEffect, useState } from 'react';
import { BookOpen, Pencil, Plus, RefreshCw } from 'lucide-react';
import { ApiError } from '../lib/api';
import { getSkill, getSkills, setSkillEnabled, type Skill, type SkillSummary } from '../lib/skills';
import SkillForm from './SkillForm';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '~/components/ui/empty';
import { Field, FieldGroup, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Switch } from '~/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

export default function SkillsView() {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [form, setForm] = useState<{ skill: Skill | null } | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getSkills(controller.signal).then((data) => {
      if (!controller.signal.aborted) { setSkills(data.skills); setError(null); }
    }).catch((error) => {
      if (!controller.signal.aborted) setError(error instanceof ApiError ? error.message : 'Could not load skills. Try refreshing.');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);

  async function edit(skill: SkillSummary) {
    setPending(skill.id); setNotice(null);
    try { setForm({ skill: await getSkill(skill.id) }); }
    catch (error) { setError(error instanceof ApiError ? error.message : 'Could not open this skill. Try refreshing.'); }
    finally { setPending(null); }
  }
  async function toggle(skill: SkillSummary, enabled: boolean) {
    setPending(skill.id); setNotice(null);
    try {
      const saved = await setSkillEnabled(skill, enabled);
      setSkills((rows) => rows?.map((row) => row.id === saved.id ? saved : row) ?? null);
      setNotice(`Skill “${saved.name}” ${saved.enabled ? 'enabled' : 'disabled'}.`);
    } catch (error) { setError(error instanceof ApiError ? error.message : 'Could not change this skill. Try refreshing.'); }
    finally { setPending(null); }
  }
  const blocked = loading || pending !== null || form !== null || error !== null;
  const matching = skills?.filter((skill) => (status === 'all' || (status === 'enabled') === skill.enabled)
    && `${skill.name} ${skill.description}`.toLowerCase().includes(query.trim().toLowerCase())) ?? [];

  return <div className="flex flex-col gap-6">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted-foreground">{skills ? `${skills.length} ${skills.length === 1 ? 'skill' : 'skills'}` : 'Loading skills…'}</p>
      <div className="flex gap-2"><Button variant="outline" disabled={loading || pending !== null || form !== null} onClick={() => setRefresh((value) => value + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
        <Button disabled={blocked || skills === null} onClick={() => { setForm({ skill: null }); setNotice(null); }}><Plus data-icon="inline-start" />Add skill</Button></div>
    </div>
    {error && <Alert variant="destructive"><AlertDescription role="alert">{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    {form && <SkillForm key={form.skill?.id ?? 'new'} skill={form.skill}
      onClose={() => { setForm(null); setRefresh((value) => value + 1); }}
      onSaved={(saved) => { setForm(null); setQuery(''); setStatus('all'); setNotice(`Skill “${saved.name}” saved.`); setRefresh((value) => value + 1); }} />}
    {skills === null && loading && <Skeleton className="h-48 w-full" />}
    {skills?.length === 0 && form === null && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><BookOpen /></EmptyMedia><EmptyTitle>No skills yet</EmptyTitle>
      <EmptyDescription>Add reusable instructions for your agents. You can update or disable them at any time.</EmptyDescription></EmptyHeader></Empty>}
    {skills !== null && skills.length > 0 && <>
      <FieldGroup className="sm:flex-row sm:items-end">
        <Field className="flex-1"><FieldLabel htmlFor="filter-skills">Filter skills</FieldLabel><Input id="filter-skills" placeholder="Search names and descriptions…" value={query} onChange={(event) => setQuery(event.target.value)} /></Field>
        <Field className="sm:w-48"><FieldLabel htmlFor="skill-status-filter">Status</FieldLabel>
          <Select value={status} onValueChange={setStatus}><SelectTrigger id="skill-status-filter"><SelectValue /></SelectTrigger><SelectContent><SelectGroup>
            <SelectItem value="all">All skills</SelectItem><SelectItem value="enabled">Enabled</SelectItem><SelectItem value="disabled">Disabled</SelectItem>
          </SelectGroup></SelectContent></Select>
        </Field>
      </FieldGroup>
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Skill</TableHead><TableHead>Status</TableHead><TableHead>Updated</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
        <TableBody>{matching.map((skill) => <TableRow key={skill.id}>
          <TableCell className="max-w-lg whitespace-normal"><p className="break-words font-medium">{skill.name}</p><p className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">{skill.description}</p></TableCell>
          <TableCell><div className="flex items-center gap-2"><Switch aria-label={`${skill.name} enabled`} checked={skill.enabled} disabled={blocked} onCheckedChange={(enabled) => void toggle(skill, enabled)} />
            <Badge variant={skill.enabled ? 'secondary' : 'outline'}>{skill.enabled ? 'Enabled' : 'Disabled'}</Badge></div></TableCell>
          <TableCell><p>{new Date(skill.updated_at).toLocaleString()}</p><p className="text-sm text-muted-foreground">Revision {skill.revision}</p></TableCell>
          <TableCell className="text-right"><Button size="sm" variant="outline" disabled={blocked} onClick={() => void edit(skill)} aria-label={`Edit ${skill.name}`}><Pencil data-icon="inline-start" />Edit</Button></TableCell>
        </TableRow>)}{matching.length === 0 && <TableRow><TableCell colSpan={4} className="text-center">No skills match these filters.</TableCell></TableRow>}</TableBody>
      </Table></div>
    </>}
  </div>;
}
