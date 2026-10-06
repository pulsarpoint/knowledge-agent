import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import type { DocumentationWorkflow } from '~/lib/documents';
import { afterEach, expect, test, vi } from 'vitest';
vi.mock('../CodeGraphExplorer', () => ({ default: () => <div>Graph inspection</div> }));
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));
import DocumentationGenerationForm from '../DocumentationGenerationForm';

const services = [{ id: 'app', name: 'Payments', service_type: 'custom', repositories: [{ id: 'repo', name: 'Application code', archived: false }] }, { id: 'db', name: 'Postgres', service_type: 'builtin', repositories: [] }];
const prompts = [
  { id: 'messaging-prompt', name: 'Messaging documentation', input_kind: 'code' },
  { id: 'api-prompt', name: 'API documentation', input_kind: 'code' },
  { id: 'overview-prompt', name: 'Application overview', input_kind: 'code' },
  { id: 'code-prompt', name: 'Code analysis and architecture', input_kind: 'code' },
  { id: 'server-prompt', name: 'Deployment and service operations', input_kind: 'server' },
  { id: 'environment-prompt', name: 'Environment architecture', input_kind: 'mixed' },
  { id: 'infrastructure-prompt', name: 'Server infrastructure architecture', input_kind: 'server' },
  { id: 'diagram-prompt', name: 'Code component diagram (D2 / TALA)', input_kind: 'code' },
].map(p => ({ ...p, enabled: true, active_version_id: p.id + '-v1' }));
afterEach(() => vi.unstubAllGlobals());
function setup(graphReady = true, workflow?: DocumentationWorkflow) {
  const saved = workflow ? structuredClone(workflow) : undefined;
  const queued = vi.fn();
  const phaseRuns: Record<string, object> = {};
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/process') && init?.method === 'POST') {
      graphReady = true;
      if (saved) saved.graph_runs = [{repository_id:'repo',job_id:'graph-job',status:'succeeded',error:null,result:{ source_hash:'fixture-hash',status:'ready',stats:{nodeCount:2,edgeCount:1},unresolved_references:0 } as DocumentationWorkflow['graph_runs'][number]['result']}];
    }
    if (saved && url.includes('/drafts/') && init?.method === 'PUT') {
      const section = url.split('/').pop() as keyof typeof saved.drafts;
      const body = JSON.parse(init.body as string);
      expect(body.previous).toEqual(saved.drafts[section] ?? null);
      saved.drafts[section] = body.content;
    }
    if (url === '/api/documentation/generations' && init?.method === 'POST') {
      const value = JSON.parse(init.body as string);
      const kind = value.units[0].kind;
      if (['overview', 'api', 'messaging'].includes(kind)) phaseRuns[kind] = { job_id: `${kind}-job`, status: 'succeeded', created_at: '2026-09-14T10:00:00Z',
        document_count: 1, code_graph_hashes: value.code_graph_hashes,
        prompt_snapshot: [{kind,version: 1,content: value.units[0].prompt_override ?? 'Active prompt',overridden: Boolean(value.units[0].prompt_override)}],
        token_usage: [{phase:kind,step:'inspection',requests:2,reported_calls:2,input_tokens:1200,output_tokens:300,total_tokens:1500},
          {phase:kind,step:'writing',requests:1,reported_calls:1,input_tokens:600,output_tokens:200,total_tokens:800}],
        result: { documents: [{id:`${kind}-doc`,version_id:`${kind}-version`,revision:1}],output_folders:[`/work/data/generated-documents/services/app/${kind}/v1`] } };
    }
    const prompt = prompts.find(p => url === '/api/documentation/prompts/' + p.id);
    const data = saved && url === '/api/documentation/workflows/'+saved.id ? structuredClone(saved) : url === '/api/code-projects' ? { projects: [{ id: 'repo', name: 'Application code', code_index: graphReady ? { source_hash: 'fixture-hash', status: 'ready', stats: { nodeCount: 2, edgeCount: 1 }, unresolved_references: 0 } : null }] } : url === '/api/services' ? { services }
      : url === '/api/environments' ? { environments: [{ id: 'dev', name: 'Development', services: services.map(s => ({ id: s.id })), servers: [{ id: 'host', name: 'Dev host', hostname: 'dev.internal' }] }, { id: 'outside', name: 'Other environment', services: [], servers: [] }] }
      : url === '/api/servers' ? { servers: [{ id: 'host', ssh_allowed: true, access_enabled: true, ssh_username: 'inspector' }] }
      : url === '/api/llm' ? { models: [{ id: 'model', name: 'Documentation model', provider: 'openai', api_format: 'responses', enabled: true }, { id: 'embedding', name: 'Embedding', provider: 'bedrock', api_format: 'embeddings', model: 'titan', enabled: true }] }
      : url === '/api/documentation/prompts' ? { prompts }
      : prompt ? { ...prompt, versions: [{ id: prompt.active_version_id, version: 1, content: 'Active ' + prompt.name }] }
      : url.startsWith('/api/documentation/generations?') ? { generations: Object.entries(phaseRuns).filter(([kind]) => kind === new URL(url, 'http://local').searchParams.get('kind')).map(([, run]) => run) }
      : url.includes('/versions/messaging-version') ? {id:'messaging-version',markdown:'# Saved messaging documentation',version:1}
      : url.includes('/versions/api-version') ? { id:'api-version', markdown:'# Saved API documentation', version:1 }
      : url.includes('/versions/overview-version') ? { id:'overview-version', markdown:'# Saved application overview', version:1 } : { job_id: 'job' };
    return { ok: true, status: init?.method === 'POST' ? 202 : 200, json: async () => data };
  });
  vi.stubGlobal('fetch', fetch);
  const view = render(<MemoryRouter><DocumentationGenerationForm onQueued={queued} workflow={workflow} /></MemoryRouter>);
  return { fetch, queued, saved, view };
}
async function select(label: string, option: string) {
  fireEvent.click(await screen.findByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}
test('diagram is independent, uses source folders and stores only a per-run prompt override', async () => {
  const { fetch, queued } = setup();
  await select('Service', 'Payments');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Code diagram (D2 / TALA)' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Application code' }));
  const prompt = await screen.findByLabelText('Prompt for this run · active version 1');
  expect(prompt).toHaveValue('Active Code component diagram (D2 / TALA)');
  fireEvent.change(prompt, { target: { value: 'Focus on queue consumers' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  const request = fetch.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(request[1]!.body as string)).toMatchObject({ scope: 'service', model_id: 'model', service_id: 'app', environment_id: null, code_project_ids: ['repo'], server_ids: [], units: [{ kind: 'diagram', prompt_id: 'diagram-prompt', prompt_version_id: 'diagram-prompt-v1', prompt_override: 'Focus on queue consumers' }] });
  expect(fetch.mock.calls.some(([url]) => url.startsWith('/api/agents'))).toBe(false);
  expect(fetch.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);
});

test('overview has an independent action, inline prompt and immutable document preview', async () => {
  const { fetch, queued } = setup(false);
  await select('Service', 'Payments');
  const prompt = await screen.findByLabelText('Overview prompt · active version 1');
  expect(prompt).toHaveValue('Active Application overview');
  expect(screen.getByRole('button', { name:'Generate overview' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name:'Generate graph' }));
  await waitFor(() => expect(screen.getByRole('button', { name:'Generate overview' })).toBeEnabled());
  expect(fetch.mock.calls.some(([url, init]) => url === '/api/documentation/generations' && init?.method === 'POST')).toBe(false);
  queued.mockClear();
  fireEvent.change(prompt, { target: { value:'Focus on the application’s users and payment flow.' } });
  fireEvent.change(screen.getByLabelText('Overview context and requests'), { target: { value:'Used by support engineers.' } });
  fireEvent.click(screen.getByRole('button', { name:'Generate overview' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  fireEvent.click(await screen.findByRole('button', { name: 'Preview overview' }));
  expect(await screen.findByText('# Saved application overview')).toBeInTheDocument();
  const posts = fetch.mock.calls.filter(([url, init]) => url === '/api/documentation/generations' && init?.method === 'POST');
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0][1]!.body as string)).toMatchObject({ service_id:'app', code_project_ids:['repo'],
    code_graph_hashes:{ repo:'fixture-hash' }, server_ids:[], environment_id:null, update_existing:true,
    instructions:'Used by support engineers.', units:[{kind:'overview',prompt_id:'overview-prompt',prompt_version_id:'overview-prompt-v1',prompt_override:'Focus on the application’s users and payment flow.'}] });
  expect(screen.getByRole('button',{name:'Regenerate overview'})).toBeEnabled();
  fireEvent.click(screen.getByRole('button',{name:'Inspect prompt and source snapshot'}));
  expect(screen.getByText('Prompt version 1 · edited for this run')).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => url.includes('/versions/overview-version'))).toBe(true);
  expect(fetch.mock.calls.some(([,init]) => init?.method === 'PUT')).toBe(false);
  await select('Service', 'Postgres');
  expect(screen.queryByText('# Saved application overview')).not.toBeInTheDocument();
});
test('built-in service requires an environment and selected server for deployment', async () => {
  const { fetch } = setup();
  await select('Service', 'Postgres');
  expect(screen.getByRole('checkbox', { name: 'Code analysis' })).toBeEnabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Deployment on an environment' }));
  expect(screen.getByRole('button', { name: 'Generate documents' })).toBeDisabled();
  await select('Environment', 'Development');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Dev host · dev.internal' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate documents' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
  expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string)).toMatchObject({ environment_id: 'dev', server_ids: ['host'], code_project_ids: [], units: [{ kind: 'deployment', prompt_id: 'server-prompt', prompt_version_id: 'server-prompt-v1' }] });
});
test('whole environment clears service selections and uses its own editable active prompt', async () => {
  const { fetch, queued } = setup();
  await select('Service', 'Payments');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Code analysis' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Application code' }));
  await select('Documentation for', 'Whole environment');
  expect(screen.queryByLabelText('Service')).not.toBeInTheDocument();
  await select('Environment', 'Development');
  const prompt = await screen.findByLabelText('Prompt for this run · active version 1');
  expect(prompt).toHaveValue('Active Environment architecture');
  fireEvent.change(prompt, { target: { value: 'Custom' } });
  fireEvent.click(screen.getByRole('button', { name: 'Reset to active prompt' }));
  expect(prompt).toHaveValue('Active Environment architecture');
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string)).toMatchObject({ scope: 'environment', service_id: null, environment_id: 'dev', code_project_ids: [], server_ids: [], units: [{ kind: 'architecture', prompt_id: 'environment-prompt' }] });
});

test('environment overview and infrastructure require separate selections and prompts', async () => {
  const { fetch, queued } = setup();
  await select('Documentation for', 'Whole environment');
  await select('Environment', 'Development');
  expect(screen.getByRole('checkbox', { name: 'Full environment architecture' })).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Server infrastructure architecture' }));
  expect(screen.getByRole('button', { name: 'Generate documents' })).toBeDisabled();
  await select('Infrastructure server', 'Dev host · dev.internal');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate documents' })).toBeEnabled());
  expect(screen.getAllByRole('textbox', { name: 'Prompt for this run · active version 1' })).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string)).toMatchObject({
    scope: 'environment', service_id: null, environment_id: 'dev', server_ids: ['host'], code_project_ids: [],
    units: [{ kind: 'architecture', prompt_id: 'environment-prompt' }, { kind: 'infrastructure', prompt_id: 'infrastructure-prompt' }],
  });
});

test('infrastructure can run alone and changing environments clears its server selection', async () => {
  const { fetch, queued } = setup();
  await select('Documentation for', 'Whole environment');
  await select('Environment', 'Development');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Full environment architecture' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Server infrastructure architecture' }));
  await select('Infrastructure server', 'Dev host · dev.internal');
  await select('Environment', 'Other environment');
  expect(screen.getByRole('button', { name: 'Generate documents' })).toBeDisabled();
  expect(screen.getByLabelText('Infrastructure server')).toHaveTextContent('Select…');
  await select('Environment', 'Development');
  expect(screen.getByRole('button', { name: 'Generate documents' })).toBeDisabled();
  await select('Infrastructure server', 'Dev host · dev.internal');
  const prompt = await screen.findByRole('textbox', { name: 'Prompt for this run · active version 1' });
  expect(prompt).toHaveValue('Active Server infrastructure architecture');
  fireEvent.change(prompt, { target: { value: 'Explain storage and ingress' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string).units).toEqual([
    { kind: 'infrastructure', prompt_id: 'infrastructure-prompt', prompt_version_id: 'infrastructure-prompt-v1', prompt_override: 'Explain storage and ingress' },
  ]);
});

test('graph generation is an explicit first phase and never starts documentation automatically', async () => {
  const { fetch, queued } = setup(false);
  await select('Service', 'Payments');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Code analysis' }));
  expect(screen.getByRole('button', { name: 'Generate documents' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Generate graph' }));
  await screen.findByText('Graph inspection');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate documents' })).toBeEnabled());
  expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST').map(([url]) => url)).toEqual(['/api/code-projects/repo/process']);
  expect(fetch.mock.calls.some(([url, init]) => url === '/api/documentation/generations' && init?.method === 'POST')).toBe(false);
  queued.mockClear();
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  const submitted = fetch.mock.calls.find(([url, init]) => url === '/api/documentation/generations' && init?.method === 'POST')!;
  expect(JSON.parse(submitted[1]!.body as string).code_graph_hashes).toEqual({ repo: 'fixture-hash' });
});


test('API phase has its own prompt, action, version preview and token usage', async () => {
  const { fetch, queued } = setup(false);
  await select('Service', 'Payments');
  const prompt = await screen.findByLabelText('API prompt · active version 1');
  expect(prompt).toHaveValue('Active API documentation');
  expect(screen.getByRole('button', { name:'Generate API documentation' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name:'Generate graph' }));
  await waitFor(() => expect(screen.getByRole('button', { name:'Generate API documentation' })).toBeEnabled());
  expect(fetch.mock.calls.some(([url, init]) => url === '/api/documentation/generations' && init?.method === 'POST')).toBe(false);
  queued.mockClear();
  fireEvent.change(prompt, { target: { value:'Document every endpoint, authorization check and state change.' } });
  fireEvent.change(screen.getByLabelText('API context and requests'), { target: { value:'Include internal endpoints.' } });
  fireEvent.click(screen.getByRole('button', { name:'Generate API documentation' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  fireEvent.click(await screen.findByRole('button', { name: 'Preview API documentation' }));
  expect(await screen.findByText('# Saved API documentation')).toBeInTheDocument();
  expect(screen.queryByText('# Saved application overview')).not.toBeInTheDocument();
  expect(screen.getByText('Token usage · 2,300 tokens')).toBeInTheDocument();
  expect(screen.getByRole('button', { name:'Generate overview' })).toBeEnabled();
  const posts = fetch.mock.calls.filter(([url, init]) => url === '/api/documentation/generations' && init?.method === 'POST');
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0][1]!.body as string)).toMatchObject({ service_id:'app', code_project_ids:['repo'],
    code_graph_hashes:{repo:'fixture-hash'}, server_ids:[], environment_id:null, update_existing:true,
    instructions:'Include internal endpoints.', units:[{kind:'api',prompt_id:'api-prompt',prompt_version_id:'api-prompt-v1',prompt_override:'Document every endpoint, authorization check and state change.'}] });
  expect(screen.getByRole('button',{name:'Regenerate API documentation'})).toBeEnabled();
  fireEvent.click(screen.getByRole('button',{name:'Inspect prompt and source snapshot'}));
  expect(screen.getByText('Prompt version 1 · edited for this run')).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => url.includes('/versions/api-version'))).toBe(true);
  expect(fetch.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);
  await select('Service', 'Postgres');
  expect(screen.queryByText('# Saved API documentation')).not.toBeInTheDocument();
});

function workflowFixture(): DocumentationWorkflow {
  return {id:'workflow-1',name:'Payments documentation',scope:'service',service_id:'app',environment_id:null,
    created_at:'2026-09-14T10:00:00Z',updated_at:'2026-09-14T10:00:00Z',phases:[],graph_runs:[],
    drafts:{form:{value:{scope:'service',model_id:'model',service_id:'app',environment_id:null,
      code_project_ids:['repo'],server_ids:[],units:[],instructions:'Saved context',update_existing:true}},
      api:{content:'Saved API prompt',instructions:'Include internal APIs'}}};
}

test('workflow restores selections and inline prompts after remount, and flushes edits before a phase starts', async () => {
  const {fetch,queued,saved,view}=setup(true,workflowFixture());
  const prompt=await screen.findByLabelText('API prompt · active version 1');
  expect(prompt).toHaveValue('Saved API prompt');
  expect(screen.getByLabelText('Additional context and requests')).toHaveValue('Saved context');
  expect(screen.queryByLabelText('Service')).not.toBeInTheDocument();
  // A graph from elsewhere is not silently adopted by this workflow.
  expect(screen.getByRole('button',{name:'Generate API documentation'})).toBeDisabled();
  fireEvent.change(prompt,{target:{value:'Follow handlers through validation and database writes.'}});
  fireEvent.change(screen.getByLabelText('Additional context and requests'),{target:{value:'Updated persistent context'}});
  await waitFor(()=>expect(saved!.drafts.api?.content).toBe('Follow handlers through validation and database writes.'));
  await waitFor(()=>expect(saved!.drafts.form?.value.instructions).toBe('Updated persistent context'));
  view.unmount();
  const reloaded=render(<MemoryRouter><DocumentationGenerationForm workflow={structuredClone(saved!)} onQueued={queued} /></MemoryRouter>);
  expect(await screen.findByLabelText('API prompt · active version 1')).toHaveValue('Follow handlers through validation and database writes.');
  expect(screen.getByLabelText('Additional context and requests')).toHaveValue('Updated persistent context');
  fireEvent.click(screen.getByRole('button',{name:'Generate graph'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'Generate API documentation'})).toBeEnabled());
  fireEvent.change(screen.getByLabelText('API context and requests'),{target:{value:'Last instant edit'}});
  fireEvent.click(screen.getByRole('button',{name:'Generate API documentation'}));
  await waitFor(()=>expect(fetch.mock.calls.some(([url,init])=>url==='/api/documentation/generations'&&init?.method==='POST')).toBe(true));
  const submitted=fetch.mock.calls.find(([url,init])=>url==='/api/documentation/generations'&&init?.method==='POST')!;
  expect(JSON.parse(submitted[1]!.body as string)).toMatchObject({workflow_id:'workflow-1',instructions:'Last instant edit'});
  expect(saved!.drafts.api?.instructions).toBe('Last instant edit');
  expect(fetch.mock.calls.some(([url])=>url.includes('workflow_id=workflow-1')&&url.includes('kind=api'))).toBe(true);
  reloaded.unmount();
});


test('messaging is an independent fourth phase with saved draft, scoped run and token usage', async () => {
  const workflow=workflowFixture();
  workflow.drafts.messaging={content:'Trace NATS handlers and acknowledgement after commit.',instructions:'Include retry paths'};
  const {fetch,saved,view,queued}=setup(true,workflow);
  expect(await screen.findByLabelText('Messaging prompt · active version 1')).toHaveValue(workflow.drafts.messaging.content);
  expect(screen.getByText('Phase 4 · Messaging documentation')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Generate messaging documentation'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Messaging prompt · active version 1'),{target:{value:'Trace every publisher and consumer through state changes.'}});
  await waitFor(()=>expect(saved!.drafts.messaging?.content).toBe('Trace every publisher and consumer through state changes.'));
  view.unmount();
  const reloaded=render(<MemoryRouter><DocumentationGenerationForm workflow={structuredClone(saved!)} onQueued={queued} /></MemoryRouter>);
  expect(await screen.findByLabelText('Messaging prompt · active version 1')).toHaveValue('Trace every publisher and consumer through state changes.');
  fireEvent.click(screen.getByRole('button',{name:'Generate graph'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'Generate messaging documentation'})).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Messaging context and requests'),{target:{value:'Check redelivery and deduplication.'}});
  fireEvent.click(screen.getByRole('button',{name:'Generate messaging documentation'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'Regenerate messaging documentation'})).toBeEnabled());
  const posts=fetch.mock.calls.filter(([url,init])=>url==='/api/documentation/generations'&&init?.method==='POST');
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0][1]!.body as string)).toMatchObject({workflow_id:'workflow-1',service_id:'app',code_project_ids:['repo'],
    code_graph_hashes:{repo:'fixture-hash'},environment_id:null,server_ids:[],update_existing:true,
    instructions:'Check redelivery and deduplication.',units:[{kind:'messaging',prompt_id:'messaging-prompt',prompt_version_id:'messaging-prompt-v1',
      prompt_override:'Trace every publisher and consumer through state changes.'}]});
  expect(saved!.drafts.messaging?.instructions).toBe('Check redelivery and deduplication.');
  fireEvent.click(await screen.findByRole('button',{name:'Preview messaging documentation'}));
  expect(await screen.findByText('# Saved messaging documentation')).toBeInTheDocument();
  expect(screen.getByText('Token usage · 2,300 tokens')).toBeInTheDocument();
  expect(screen.getByText('Messaging documentation · Source inspection')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Generate API documentation'})).toBeEnabled();
  expect(screen.getByRole('button',{name:'Generate overview'})).toBeEnabled();
  expect(fetch.mock.calls.some(([url])=>url.includes('kind=messaging')&&url.includes('workflow_id=workflow-1'))).toBe(true);
  reloaded.unmount();
});
