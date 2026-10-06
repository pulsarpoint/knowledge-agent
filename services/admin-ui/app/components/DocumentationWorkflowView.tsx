import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { createDocumentationWorkflow, getDocumentationWorkflow, getDocumentationWorkflows, type DocumentationWorkflow } from '~/lib/documents';
import { getServices, getEnvironments } from '~/lib/api';
import { getLlmSettings, codeCompatible } from '~/lib/llm';
import CodeProjectDocumentationForm from './CodeProjectDocumentationForm';
import DocumentationGenerationForm from './DocumentationGenerationForm';
import DocumentationGenerationsView from './DocumentationGenerationsView';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Field, FieldGroup, FieldLabel } from '~/components/ui/field';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

const titles: Record<string,string> = {graph:'1 · Code graph',overview:'2 · Application overview',api:'3 · API documentation',messaging:'4 · Messaging documentation',code:'Code analysis',diagram:'Code diagram',deployment:'Deployment',deployment_diagram:'Deployment diagram',infrastructure:'Infrastructure',architecture:'Environment architecture'};

function graphStatus(runs: DocumentationWorkflow['graph_runs']) {
  for (const status of ['running','queued','waiting_input','failed','cancelled']) {
    if (runs.some(run => run.status === status)) return status;
  }
  return runs.length ? 'succeeded' : 'Not started';
}

export function DocumentationWorkflowsView() {
  const [rows,setRows] = useState<DocumentationWorkflow[] | null>(null);
  const [offset,setOffset] = useState(0);
  const [error,setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    getDocumentationWorkflows(offset,controller.signal).then(result => { if (!controller.signal.aborted) setRows(result.workflows); }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  },[offset]);
  return <div className="flex flex-col gap-5"><div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-semibold">Documentation workflows</h1><Button asChild><Link to="/documentation/generations/new">New documentation</Link></Button></div><p className="text-muted-foreground">Open a saved workflow to inspect its phases, continue generation or repeat a phase.</p>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {rows === null ? <Skeleton className="h-40" /> : <Table><TableHeader><TableRow><TableHead>Documentation</TableHead><TableHead>Phases</TableHead><TableHead>Updated</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.id}><TableCell><Link className="underline" to={`/documentation/generations/${row.id}`}>{row.name}</Link></TableCell><TableCell><div className="flex flex-wrap gap-2">{row.graph_runs.length > 0 && <Badge variant="secondary">Code graph · {graphStatus(row.graph_runs)}</Badge>}{row.phases.length ? row.phases.map(phase => <Badge key={phase.kind} variant={phase.status === 'failed' ? 'destructive':'secondary'}>{titles[phase.kind]} · {phase.status}</Badge>) : row.graph_runs.length ? null : 'Not started'}</div></TableCell><TableCell>{new Date(row.updated_at).toLocaleString()}</TableCell></TableRow>)}</TableBody></Table>}
    {rows?.length === 0 && <p>No saved documentation workflows.</p>}
    {(offset > 0 || rows?.length === 50) && <div className="flex gap-2"><Button variant="outline" disabled={!offset} onClick={() => setOffset(n => n-50)}>Previous</Button><Button variant="outline" disabled={(rows?.length ?? 0)<50} onClick={() => setOffset(n => n+50)}>Next</Button></div>}
    <DocumentationGenerationsView scope="code_project" title="Code project documents" />
  </div>;
}

export function NewDocumentationWorkflow() {
  const navigate=useNavigate();
  const [data,setData]=useState<{services:{id:string;name:string}[];environments:{id:string;name:string}[];models:{id:string;name:string}[]} | null>(null);
  const [scope,setScope]=useState<'service'|'environment'|'code_project'>('service');
  const [subject,setSubject]=useState('');
  const [model,setModel]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  useEffect(() => { let stopped=false; Promise.all([getServices(),getEnvironments(),getLlmSettings()]).then(([s,e,m]) => {
    if (stopped) return;
    const models=m.models.filter(v => v.enabled && v.api_format!=='embeddings' && codeCompatible(v));
    setData({services:s.services,environments:e.environments,models}); if (models.length===1) setModel(models[0].id);
  }).catch(e => {if (!stopped) setError(e.message);}); return () => {stopped=true;}; },[]);
  function choose(label:string,value:string,items:{id:string;name:string}[],change:(value:string)=>void) {
    return <Field><FieldLabel htmlFor={`workflow-${label}`}>{label}</FieldLabel><Select value={value} onValueChange={change}><SelectTrigger id={`workflow-${label}`}><SelectValue placeholder="Select…" /></SelectTrigger><SelectContent><SelectGroup>{items.map(item => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>;
  }
  return <div className="flex max-w-2xl flex-col gap-5"><form className="flex flex-col gap-5" onSubmit={async e => {e.preventDefault();if (scope === 'code_project') return;setBusy(true);setError('');try {
    const row=await createDocumentationWorkflow({scope:scope as 'service' | 'environment',model_id:model,service_id:scope==='service'?subject:null,environment_id:scope==='environment'?subject:null});
    navigate(`/documentation/generations/${row.id}`);
  } catch(e) {setError(e instanceof Error?e.message:'Could not create workflow');} finally {setBusy(false);} }}>
    <Link className="underline" to="/documentation/generations">Documentation workflows</Link><h1 className="text-2xl font-semibold">New documentation</h1>
    <p>{scope==='code_project'?'Generate one document for a code project with a prompt you choose and save it into a documentation folder.':'First create a saved workflow. Source selection, prompts and each generation attempt will belong to its permanent URL.'}</p>
    {data ? <fieldset disabled={busy}><FieldGroup>{choose('Scope',scope,[{id:'service',name:'Service'},{id:'environment',name:'Whole environment'},{id:'code_project',name:'Code project'}],v=>{setScope(v as typeof scope);setSubject('');})}
      {scope!=='code_project'&&choose(scope==='service'?'Service':'Environment',subject,scope==='service'?data.services:data.environments,setSubject)}{choose('Model',model,data.models,setModel)}</FieldGroup></fieldset> : <Skeleton className="h-40" />}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {scope!=='code_project'&&<Button type="submit" className="self-start" disabled={busy||!subject||!model}>{busy?'Creating…':'Create workflow'}</Button>}
  </form>
  {scope==='code_project' && data && <CodeProjectDocumentationForm modelId={model} onQueued={() => navigate('/documentation/generations')} />}
  </div>;
}

export default function DocumentationWorkflowView() {
  const {workflowId}=useParams();
  const [row,setRow]=useState<DocumentationWorkflow|null>(null);
  const [error,setError]=useState('');
  const [refresh,setRefresh]=useState(0);
  useEffect(()=>{setRow(null);setError('');},[workflowId]);
  useEffect(() => {
    const controller=new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function load() {try {
      const next=await getDocumentationWorkflow(workflowId!,controller.signal);
      if (controller.signal.aborted) return;
      setRow(next);setError('');
      if ([...next.phases,...next.graph_runs].some(p=>['queued','running'].includes(p.status))) timer=setTimeout(load,2000);
    } catch(e) {if(!controller.signal.aborted)setError(e instanceof Error?e.message:'Could not open workflow');}}
    void load();return()=>{controller.abort();clearTimeout(timer);};
  },[workflowId,refresh]);
  if (error) return <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>;
  if (!row) return <Skeleton className="h-60" />;
  const kinds=row.scope==='service'?['graph','overview','api','messaging',...row.phases.map(p=>p.kind)]:['architecture','infrastructure'];
  return <div className="flex flex-col gap-5"><Link className="underline" to="/documentation/generations">Documentation workflows</Link><h1 className="text-2xl font-semibold">{row.name}</h1>
    <p className="text-sm text-muted-foreground">Settings and prompts save automatically. This URL restores your workflow and its phase attempts after refresh.</p>
    <Table><TableHeader><TableRow><TableHead>Phase</TableHead><TableHead>Latest attempt</TableHead><TableHead>Actions</TableHead></TableRow></TableHeader><TableBody>{[...new Set(kinds)].map(kind=>{
      const phase=row.phases.find(p=>p.kind===kind);const status=kind==='graph'?graphStatus(row.graph_runs):phase?.status??'Not started';
      return <TableRow key={kind}><TableCell>{titles[kind]}</TableCell><TableCell><Badge variant={status==='failed'?'destructive':'secondary'}>{status}</Badge>{phase?.error&&<p className="text-sm text-destructive">{phase.error}</p>}</TableCell><TableCell><Button asChild variant="outline" size="sm"><a href={`#phase-${['graph','overview','api','messaging'].includes(kind)?kind:'additional'}`}>Inspect / rerun</a></Button></TableCell></TableRow>;
    })}</TableBody></Table>
    <div id={row.scope==='environment'?'phase-additional':undefined}><DocumentationGenerationForm key={row.id} workflow={row} onQueued={()=>setRefresh(n=>n+1)} /></div>
    <DocumentationGenerationsView key={`${row.id}:${refresh}`} workflowId={row.id} />
  </div>;
}
