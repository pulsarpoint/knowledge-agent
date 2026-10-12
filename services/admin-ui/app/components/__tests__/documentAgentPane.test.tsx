import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import DocumentEditorView from '../DocumentEditorView';

vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const pane = vi.hoisted(() => ({ mounts: 0, props: [] as any[] }));
vi.mock('../AgentPane', async () => {
  const { useEffect } = await import('react');
  return { default: (props: any) => {
    useEffect(() => { pane.mounts += 1; }, []);
    pane.props.push(props);
    return <section aria-label="Ask agent">
      <p>{`run ${props.initialRunId ?? 'none'}`}</p>
      <button type="button" onClick={() => props.confirmUnsaved(() => { pane.props.push({ started: true }); })}>Send message</button>
      <button type="button" onClick={() => props.onDraftChange({ id: 'd1', document_id: 'doc-1', status: 'open' }, `v${pane.props.length}`)}>Agent saved</button>
      <button type="button" onClick={() => props.onRunChange(null)}>New chat</button>
    </section>;
  } };
});
const NativeRequest = globalThis.Request;
beforeEach(() => { vi.stubGlobal('Request', class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
}); try { localStorage.clear(); } catch { /* none */ } pane.mounts = 0; pane.props = []; });
afterEach(() => vi.unstubAllGlobals());

const at = '2026-10-12T10:00:00Z';
const resources = { code_projects: [], services: [], servers: [], deployments: [], environments: [] };
const version = { id: 'v2', version: 2, title: 'Runbook', description: '', purpose: 'runbook', audience: 'both', tags: [],
  markdown: '# Runbook', content_hash: 'a'.repeat(64), source_snapshot: resources, origin: 'manual', base_version_id: 'v1', created_at: at };
const document = { id: 'doc-1', slug: 'runbook', title: 'Runbook', description: '', purpose: 'runbook', audience: 'both', tags: [],
  code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [], environment_ids: [], revision: 2, version: 2,
  current_version_id: 'v2', published_version_id: null, content_hash: 'a', created_at: at, updated_at: at, current_version: version,
  versions: [{ ...version, from_draft: false }] };
const draft = { id: 'd1', document_id: 'doc-1', slug: null, folder: null, title: 'Runbook', origin: 'agent', session_id: 'r1', session_title: 'x',
  session_purpose: 'revision', author: null, edited: false, status: 'open', revision: 3, base_version_id: 'v1', base_version: 1, live_version: 2,
  stale: true, created_at: at, updated_at: at, description: '', purpose: 'runbook', audience: 'both', tags: [], markdown: '# New',
  resource_ids: {}, live_markdown: '# Runbook', document_title: 'Runbook', promoted_version_id: null, decided_at: null, sources: [] };
const STALE = 'This draft was based on version 1; the live version is now 2.';
const base: Record<string, (body: any) => [number, unknown]> = {
  'GET /api/documentation/documents/doc-1': () => [200, document],
  'GET /api/documentation/resources': () => [200, resources],
  'GET /api/agent-sessions/options?kind=revision&document_id=doc-1': () => [200, { models: [], default_model_id: null, prompts: [],
    default_prompt: { id: null, name: 'Built-in', content: 'x' }, resources: { servers: [], services: [], environments: [], code_projects: [], collections: [] },
    tools: [], fixed_tools: [], experts: [] }],
  'GET /api/documentation/drafts/d1': () => [200, draft],
};
function api(routes: Record<string, (body: any) => [number, unknown]>) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url}`;
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ key, body });
    if (!(key in routes)) throw new Error(`unexpected ${key}`);
    const [status, value] = routes[key](body);
    return new Response(JSON.stringify(value), { status });
  }));
  return calls;
}
function show(url: string) {
  // as in routes.ts: the run link is a child of the document page
  const router = createMemoryRouter([{ path: '/documents/:documentId', element: <DocumentEditorView />,
    children: [{ path: 'agent/:runId', element: null }] }], { initialEntries: [url] });
  render(<RouterProvider router={router} />);
  return router;
}

test('Ask agent toggles the pane, and the choice is remembered', async () => {
  api(base);
  show('/documents/doc-1');
  const toggle = await screen.findByRole('button', { name: 'Ask agent' });
  expect(toggle).toHaveAttribute('aria-pressed', 'false');
  expect(screen.queryByRole('region', { name: 'Ask agent' })).toBeNull();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('region', { name: 'Ask agent' })).toBeInTheDocument();
  expect(screen.getByRole('separator', { name: 'Resize the agent pane' })).toBeInTheDocument();
  expect(localStorage.getItem('ask-agent-pane')).toBe('open');
});

test('the run deep link opens the pane on that run', async () => {
  api(base);
  show('/documents/doc-1/agent/r7');
  expect(await screen.findByText('run r7')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Ask agent' })).toHaveAttribute('aria-pressed', 'true');
});

test('the first message asks about unsaved changes first', async () => {
  api(base);
  show('/documents/doc-1/agent/r7');
  fireEvent.change(await screen.findByLabelText('Markdown'), { target: { value: '# Edited, not saved' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  const dialog = await screen.findByRole('alertdialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Keep editing' }));
  expect(pane.props.some(p => p.started)).toBe(false);
  expect(screen.getByLabelText('Markdown')).toHaveValue('# Edited, not saved');
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Discard changes' }));
  expect(pane.props.some(p => p.started)).toBe(true);
});

test('the draft bar shows the agent draft; Compare opens it; a stale Promote asks, then promotes', async () => {
  let promoted = 0;
  const calls = api({ ...base,
    'POST /api/documentation/drafts/d1/promote': body => { promoted += 1;
      return body.confirm_stale ? [200, { draft: { ...draft, status: 'promoted' }, document_id: 'doc-1', version_id: 'v3', revision: 3, export_error: null }]
        : [409, { error: { message: STALE, code: 'STALE_BASE' } }]; },
    'GET /api/documentation/documents/doc-1/drafts': () => [200, { drafts: [draft] }] });
  const router = show('/documents/doc-1/agent/r1');
  fireEvent.click(await screen.findByRole('button', { name: 'Agent saved' }));
  const bar = await screen.findByRole('region', { name: 'Agent draft' });
  expect(await within(bar).findByText('Agent draft · based on version 1')).toBeInTheDocument();
  expect(within(bar).getByText('stale')).toBeInTheDocument();
  fireEvent.click(within(bar).getByRole('button', { name: 'Promote' }));
  expect(await within(bar).findByText(STALE)).toBeInTheDocument();
  fireEvent.click(within(bar).getByRole('button', { name: 'Promote anyway' }));
  await waitFor(() => expect(promoted).toBe(2));
  expect(calls.filter(c => c.key === 'POST /api/documentation/drafts/d1/promote').map(c => c.body.confirm_stale)).toEqual([false, true]);
  expect(await screen.findByText('Draft promoted to version 3.')).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Agent draft' })).toBeNull();  // the bar goes at once
  expect(pane.mounts).toBe(1);  // the pane keeps its conversation when a draft is promoted
  fireEvent.click(screen.getByRole('button', { name: 'Agent saved' }));
  fireEvent.click(within(await screen.findByRole('region', { name: 'Agent draft' })).getByRole('button', { name: 'Compare' }));
  await waitFor(() => expect(router.state.location.search).toBe('?tab=drafts&draft=d1'));
});

test('a run link followed on the page switches the pane; New chat takes the run out of the URL', async () => {
  api({ ...base, 'GET /api/documentation/documents/doc-1/drafts': () => [200, { drafts: [] }] });
  const router = show('/documents/doc-1/agent/r1');
  expect(await screen.findByText('run r1')).toBeInTheDocument();
  fireEvent.change(await screen.findByLabelText('Markdown'), { target: { value: '# Edited, not saved' } });
  await act(() => router.navigate('/documents/doc-1/agent/r2?tab=drafts'));
  expect(await screen.findByText('run r2')).toBeInTheDocument();
  expect(pane.mounts).toBe(1);  // the page stays: the pane and the editor's unsaved text
  expect(screen.getByLabelText('Markdown')).toHaveValue('# Edited, not saved');
  fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
  await waitFor(() => expect(router.state.location.pathname).toBe('/documents/doc-1'));
  expect(router.state.location.search).toBe('?tab=drafts');
  expect(screen.getByRole('region', { name: 'Ask agent' })).toBeInTheDocument();
});

test('a run link opens a closed pane', async () => {
  api({ ...base, 'GET /api/agent-sessions?document_id=doc-1': () => [200, { sessions: [] }] });
  const router = show('/documents/doc-1');
  await screen.findByRole('button', { name: 'Ask agent' });
  expect(screen.queryByRole('region', { name: 'Ask agent' })).toBeNull();
  await act(() => router.navigate('/documents/doc-1/agent/r3'));
  expect(await screen.findByText('run r3')).toBeInTheDocument();
});

test('Promote sends the draft\'s latest revision after the agent saved into it again', async () => {
  let revision = 3;
  const calls = api({ ...base, 'GET /api/documentation/drafts/d1': () => [200, { ...draft, stale: false, revision }],
    'POST /api/documentation/drafts/d1/promote': body => body.revision === revision
      ? [200, { draft: { ...draft, status: 'promoted' }, document_id: 'doc-1', version_id: 'v3', revision: 3, export_error: null }]
      : [409, { error: { message: 'The draft changed.', code: 'CONFLICT' } }],
    'GET /api/documentation/documents/doc-1/drafts': () => [200, { drafts: [] }] });
  show('/documents/doc-1/agent/r1');
  fireEvent.click(await screen.findByRole('button', { name: 'Agent saved' }));
  const bar = await screen.findByRole('region', { name: 'Agent draft' });
  await within(bar).findByText('Agent draft · based on version 1');
  revision = 4;  // the agent saves into the same draft again
  fireEvent.click(within(bar).getByRole('button', { name: 'Promote' }));
  expect(await screen.findByText('Draft promoted to version 3.')).toBeInTheDocument();
  expect(calls.filter(c => c.key === 'POST /api/documentation/drafts/d1/promote').map(c => c.body.revision)).toEqual([4]);
});

test('the bar reloads the draft after each of the agent\'s turns', async () => {
  let loads = 0;
  api({ ...base, 'GET /api/documentation/drafts/d1': () => { loads += 1; return [200, draft]; } });
  show('/documents/doc-1/agent/r1');
  fireEvent.click(await screen.findByRole('button', { name: 'Agent saved' }));
  await waitFor(() => expect(loads).toBe(1));
  fireEvent.click(screen.getByRole('button', { name: 'Agent saved' }));  // the same draft, a new version
  await waitFor(() => expect(loads).toBe(2));
});
