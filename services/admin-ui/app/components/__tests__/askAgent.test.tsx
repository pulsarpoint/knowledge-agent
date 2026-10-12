import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import AskAgentSheet from '../AskAgentSheet';

const NativeRequest = globalThis.Request;
beforeEach(() => vi.stubGlobal('Request', class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
}));
afterEach(() => vi.unstubAllGlobals());

const options = {
  models: [{ id: 'm1', name: 'GPT' }, { id: 'm2', name: 'Claude' }], default_model_id: 'm2',
  prompts: [{ id: 'p1', name: 'Careful reviser', content: 'You revise carefully.' }],
  default_prompt: { id: null, name: 'Built-in', content: 'Built-in prompt.' },
  resources: {
    servers: [{ id: 's1', name: 'web-1', linked: true, usable: true, reason: null },
      { id: 's2', name: 'prod-1', linked: false, usable: false, reason: 'SSH is not set up for this server.' }],
    services: [{ id: 'v1', name: 'Payments', linked: true }], environments: [], code_projects: [{ id: 'c1', name: 'Shop', linked: false }], collections: [] },
  tools: [{ name: 'ssh_run', label: 'SSH', summary: 'Run a command', group: 'servers', needs: 'ssh', requires: 'at least one server' },
    { name: 'read_file', label: 'Read file', summary: 'Read a file', group: 'code', needs: 'files', requires: 'a code project or a collection' },
    { name: 'get_service_info', label: 'Service info', summary: 'A service', group: 'inventory', needs: null, requires: null }],
  fixed_tools: [{ name: 'get_doc', label: 'Read the document' }, { name: 'save_doc', label: 'Save the document' }, { name: 'edit_doc', label: 'Edit the document' }],
  experts: [{ id: 'e1', name: 'Payments expert', when_to_use: 'Payments questions' }],
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
const OPTIONS = 'GET /api/agent-sessions/options?kind=revision&document_id=doc-1';
function show() {
  const router = createMemoryRouter([
    { path: '/documents/:documentId', element: <AskAgentSheet documentId="doc-1" documentTitle="Runbook" onClose={() => {}} /> },
    { path: '/documents/:documentId/agent/:runId', element: <p>Run page</p> }], { initialEntries: ['/documents/doc-1'] });
  render(<RouterProvider router={router} />);
  return router;
}
const expected = {
  kind: 'revision', target: { document_id: 'doc-1' }, instruction: 'Add log rotation.', model_id: 'm2', prompt_id: 'p1',
  system_prompt: 'You revise carefully.', resources: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: [], collection_ids: [] },
  tools: ['ssh_run', 'get_service_info'], expert_ids: [], subagents: false };

test('defaults: linked resources ticked, applicable tools ticked, fixed tools shown, unusable server disabled', async () => {
  api({ [OPTIONS]: () => [200, options] });
  show();
  expect(await screen.findByRole('checkbox', { name: 'web-1' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Payments' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'prod-1' })).toBeDisabled();
  expect(screen.getByText('SSH is not set up for this server.')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'SSH' })).toBeChecked();
  expect(screen.queryByRole('checkbox', { name: 'Read file' })).toBeNull();  // no code project or collection ticked
  expect(screen.getByRole('checkbox', { name: 'Read the document' })).toBeDisabled();
  expect(screen.getByRole('switch', { name: 'Allow subagents' })).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Shop' }));
  expect(screen.getByRole('checkbox', { name: 'Read file' })).toBeChecked();
});

test('Preview shows the JSON and Start opens the run page', async () => {
  const calls = api({ [OPTIONS]: () => [200, options],
    'POST /api/agent-sessions/preview': () => [200, { config: { model: { name: 'Claude' } }, first_message: 'Add log rotation.' }],
    'POST /api/agent-sessions': () => [201, { id: 'run-1' }] });
  const router = show();
  fireEvent.change(await screen.findByLabelText('Instruction'), { target: { value: 'Add log rotation.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  const preview = await screen.findByLabelText('Configuration preview');
  expect(preview.textContent).toContain('"first_message": "Add log rotation."');
  expect(calls.find(c => c.key === 'POST /api/agent-sessions/preview')?.body).toEqual(expected);
  fireEvent.click(screen.getByRole('button', { name: 'Start' }));
  await waitFor(() => expect(router.state.location.pathname).toBe('/documents/doc-1/agent/run-1'));
  expect(calls.find(c => c.key === 'POST /api/agent-sessions')?.body).toEqual(expected);
});

test('a refusal is shown in the sheet', async () => {
  api({ [OPTIONS]: () => [200, options],
    'POST /api/agent-sessions': () => [400, { error: { message: 'ssh_run needs at least one server.' } }] });
  show();
  fireEvent.change(await screen.findByLabelText('Instruction'), { target: { value: 'Add log rotation.' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'web-1' }));
  fireEvent.click(screen.getByRole('button', { name: 'Start' }));
  expect(await screen.findByText('ssh_run needs at least one server.')).toBeInTheDocument();
});
