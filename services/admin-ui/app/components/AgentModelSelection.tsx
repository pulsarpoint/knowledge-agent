import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { saveProfileModel, type AgentProfile } from '../lib/profiles';
import { ApiError } from '../lib/api';
import { codeCompatible, getLlmSettings, saveAgentModel, type LlmSettings } from '../lib/llm';
import { agentNames, type AgentName } from '../lib/prompts';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '~/components/ui/field';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Spinner } from '~/components/ui/spinner';

export default function AgentModelSelection({ agent = "code", profile, onSaved, onRefresh }: { agent?: AgentName; profile?: AgentProfile; onRefresh?: () => void; onSaved: (profile?: AgentProfile) => void }) {
  const [settings, setSettings] = useState<LlmSettings>();
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const field = `${agent}_model_id` as const;

  useEffect(() => {
    let live = true;
    setLoading(true); setError(undefined); setNotice(undefined);
    getLlmSettings().then(value => {
      if (!live) return;
      setSettings(value); setSelected(profile ? profile.model_id : value.assignments[field] ?? null);
    }).catch(reason => {
      if (live) setError(reason instanceof ApiError ? reason.message : 'Could not load agent models.');
    }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [field, reload]);

  useEffect(() => { if (profile) setSelected(profile.model_id); }, [profile?.model_id]);

  async function save() {
    if (!settings || loading || saving) return;
    setSaving(true); setError(undefined); setNotice(undefined);
    try {
      if (profile) {
        const saved = await saveProfileModel(profile, selected);
        onSaved(saved);
      } else {
        const assignments = await saveAgentModel(agent, selected, settings.assignments.revision);
        setSettings({ ...settings, assignments });
        onSaved();
      }
      setNotice(`${profile?.name ?? agentNames[agent]} model saved. New requests use this selection.`);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Could not save the agent model.');
    } finally { setSaving(false); }
  }

  const savedId = profile ? profile.model_id : settings?.assignments[field] ?? null;
  const models = settings?.models.filter(model => model.api_format !== 'embeddings' && (model.id === savedId || model.enabled && (profile?.is_coordinator || !profile && agent === 'search' || codeCompatible(model)))) ?? [];
  return <Card>
    <CardHeader><CardTitle>{profile ? "Model connection" : agentNames[agent] + " model"}</CardTitle><CardDescription>Choose the model connection this agent uses. Changes apply to new requests.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-3">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
      {loading ? <Skeleton className="h-10 w-full" /> : settings && <FieldGroup>
        <Field data-disabled={saving}><FieldLabel htmlFor={`agent-model-${agent}`}>Model connection</FieldLabel>
          <Select value={selected ?? 'none'} disabled={saving} onValueChange={value => { setSelected(value === 'none' ? null : value); setNotice(undefined); }}>
            <SelectTrigger id={`agent-model-${agent}`}><SelectValue /></SelectTrigger>
            <SelectContent><SelectGroup><SelectItem value="none">Not configured</SelectItem>
              {models.map(model => <SelectItem key={model.id} value={model.id} disabled={!model.enabled || !profile?.is_coordinator && (profile || agent !== 'search') && !codeCompatible(model)}>{model.name}{!model.enabled && ' (disabled)'}</SelectItem>)}
            </SelectGroup></SelectContent>
          </Select>
          <FieldDescription>{profile?.is_coordinator || !profile && agent === 'search' ? 'Select an enabled model with tool support.' : 'Select an enabled Responses connection or a Claude model.'} {models.length === 0 && 'Add a compatible connection in LLM Settings to get started.'}</FieldDescription>
        </Field>
      </FieldGroup>}
    </CardContent>
    <CardFooter className="flex-wrap gap-2">
      <Button disabled={loading || saving || !settings || selected === savedId} onClick={() => void save()}>{saving && <Spinner />}Save model</Button>
      <Button variant="outline" disabled={loading || saving} onClick={() => { onRefresh?.(); setReload(value => value + 1); }}>Refresh models</Button>
      <Button variant="link" asChild><Link to="/llm-settings">Manage model connections</Link></Button>
    </CardFooter>
  </Card>;
}
