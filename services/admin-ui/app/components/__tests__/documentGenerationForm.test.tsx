import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import { NewGeneration } from '../DocumentGenerationForm';

afterEach(() => vi.unstubAllGlobals());
const MODELS = [
  { id: 'gpt', name: 'GPT', provider: 'openai', model: 'gpt-5', api_format: 'responses', enabled: true },
  { id: 'chat', name: 'Chat', provider: 'openai', model: 'gpt-4o', api_format: 'chat_completions', enabled: true },
  { id: 'off', name: 'Old', provider: 'openai', model: 'gpt-5', api_format: 'responses', enabled: false }];
const bodies: Record<string, unknown> = {
  '/api/llm': { models: MODELS, assignments: {} },
  '/api/documentation/prompts': { prompts: [
    { id: 'p1', name: 'API reference', input_kind: 'code', enabled: true, active_version_id: 'v1' },
    { id: 'p2', name: 'Runbook', input_kind: 'service', enabled: true, active_version_id: 'v3' },
    { id: 'p3', name: 'Draft only', input_kind: 'mixed', enabled: false, active_version_id: null }] },
  '/api/documentation/prompts/p1': { id: 'p1', active_version_id: 'v1', versions: [
    { id: 'v2', version: 2, content: 'Unactivated draft' }, { id: 'v1', version: 1, content: 'Write an API reference.' }] },
  '/api/documentation/prompts/p2': { id: 'p2', active_version_id: 'v3', versions: [{ id: 'v3', version: 1, content: 'Write a runbook.' }] },
  '/api/code-projects': { projects: [{ id: 'repo', name: 'Payments API' }, { id: 'worker', name: 'Payments worker' }] },
  '/api/environments': { environments: [{ id: 'env', name: 'Production' }] },
  '/api/services': { services: [{ id: 'svc', name: 'Payments' }] },
  '/api/servers': { servers: [{ id: 'srv', name: 'prod-1', hostname: '10.0.0.5' }] },
  '/api/rag/collections?status=false': { collections: [{ id: 'runbooks', name: 'runbooks' }] },
  '/api/documentation/folders': { folders: [] },
};
type Pending = { body: Record<string, string[]>; resolve: (r: Response) => void };

function setup({ models = MODELS, hold = false, error = '', failPrompt = '', contextFails = false } = {}) {
  const posts: Record<string, unknown>[] = [];
  const held: Pending[] = [];
  const contextText = (body: Record<string, string[]>) =>
    `# What you are documenting\n\n${Object.values(body).flat().join(', ')}`;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/claude-code/context') {
      const body = JSON.parse(init!.body as string);
      if (contextFails) return Response.json({ error: { message: 'Context service is down' } }, { status: 500 });
      if (hold) return new Promise<Response>(resolve => held.push({ body, resolve }));
      return Response.json({ text: contextText(body) });
    }
    if (url === '/api/claude-code/sessions' && init?.method === 'POST') {
      posts.push(JSON.parse(init.body as string));
      return error ? Response.json({ error: { message: error } }, { status: 400 }) : Response.json({ id: 'session-1' }, { status: 201 });
    }
    if (url === '/api/llm') return Response.json({ models, assignments: {} });
    if (failPrompt && url === `/api/documentation/prompts/${failPrompt}`) return Response.json({ error: { message: 'Prompt is gone' } }, { status: 500 });
    return Response.json(bodies[url] ?? {});
  }));
  render(<MemoryRouter initialEntries={['/documentation/generations/new']}><Routes>
    <Route path="/documentation/generations/new" element={<NewGeneration />} />
    <Route path="/documentation/generations/:sessionId" element={<div>Session page</div>} />
  </Routes></MemoryRouter>);
  return { posts, held, contextText };
}
const check = (name: string) => fireEvent.click(screen.getByRole('checkbox', { name }));
async function select(label: string, option: string) {
  fireEvent.click(await screen.findByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}
/** The one match of the text that is not inside the collapsed (hidden) Agent context panel. */
const shown = (text: string) => {
  const found = screen.getAllByText(text).filter(element => !element.closest('[hidden]'));
  expect(found).toHaveLength(1);
  return found[0];
};
async function openContext() { fireEvent.click(await screen.findByRole('button', { name: 'Agent context' })); }

test('there is no agent picker; the context follows the selection and Generate sends everything', async () => {
  const { posts } = setup();
  await openContext();
  expect(screen.queryByLabelText('Agent')).toBeNull();
  expect(screen.getByLabelText('System prompt')).toHaveValue(
    'You write technical documentation for software systems. Read the code, documents and servers you were given before you write. Cite evidence as file:line, document path or command. Say what you could not verify.');
  fireEvent.click(screen.getByLabelText('Model'));
  expect(await screen.findByRole('option', { name: 'GPT' })).toBeVisible();
  for (const hidden of ['Chat', 'Old']) expect(screen.queryByRole('option', { name: hidden })).toBeNull();
  fireEvent.click(screen.getByRole('option', { name: 'GPT' }));
  check('Code'); check('Payments API');
  await waitFor(() => expect(screen.getByLabelText('Context')).toHaveValue('# What you are documenting\n\nrepo'));
  check('Services'); check('Payments');
  await waitFor(() => expect(screen.getByLabelText('Context')).toHaveValue('# What you are documenting\n\nrepo, svc'));
  await select('Prompt', 'API reference · code');
  await waitFor(() => expect(screen.getByLabelText('Prompt text')).toHaveValue('Write an API reference.'));
  expect(screen.queryByRole('option', { name: /Draft only/ })).toBeNull();
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments API' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([{ model_id: 'gpt', code_project_ids: ['repo'], collection_ids: [], environment_ids: [],
    service_ids: ['svc'], server_ids: [], system_prompt: expect.stringContaining('You write technical documentation'),
    context: '# What you are documenting\n\nrepo, svc', prompt: 'Write an API reference.', prompt_id: 'p1',
    document_name: 'Payments API', folder: 'payments-api-docs' }]);
});

test('an edited context stops following the selection until Regenerate is confirmed', async () => {
  setup();
  await openContext();
  check('Code'); check('Payments API');
  await waitFor(() => expect(screen.getByLabelText('Context')).toHaveValue('# What you are documenting\n\nrepo'));
  fireEvent.change(screen.getByLabelText('Context'), { target: { value: 'My own context' } });
  expect(screen.getByText('Edited — no longer follows your selection')).toBeVisible();
  check('Payments worker');
  await new Promise(resolve => setTimeout(resolve, 400));
  expect(screen.getByLabelText('Context')).toHaveValue('My own context');
  fireEvent.click(screen.getByRole('button', { name: 'Regenerate from selection' }));
  expect(screen.getByText('Replace your edits?')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Keep my edits' }));
  expect(screen.getByLabelText('Context')).toHaveValue('My own context');
  fireEvent.click(screen.getByRole('button', { name: 'Regenerate from selection' }));
  fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
  await waitFor(() => expect(screen.getByLabelText('Context')).toHaveValue('# What you are documenting\n\nrepo, worker'));
});

test('a stale context response never replaces a newer one', async () => {
  const { held, contextText } = setup({ hold: true });
  await openContext();
  check('Code'); check('Payments API');
  await waitFor(() => expect(held).toHaveLength(1));
  check('Payments worker');
  await waitFor(() => expect(held).toHaveLength(2));
  held[1].resolve(Response.json({ text: contextText(held[1].body) }));
  await waitFor(() => expect(screen.getByLabelText('Context')).toHaveValue('# What you are documenting\n\nrepo, worker'));
  held[0].resolve(Response.json({ text: contextText(held[0].body) }));
  await new Promise(resolve => setTimeout(resolve, 50));
  expect(screen.getByLabelText('Context')).toHaveValue('# What you are documenting\n\nrepo, worker');
});

test('switching prompts never drops edits without asking; Write my own starts empty', async () => {
  setup();
  await select('Prompt', 'API reference · code');
  await waitFor(() => expect(screen.getByLabelText('Prompt text')).toHaveValue('Write an API reference.'));
  fireEvent.change(screen.getByLabelText('Prompt text'), { target: { value: 'Write an API reference for partners.' } });
  await select('Prompt', 'Runbook · service');
  expect(screen.getByText('Replace your edited prompt?')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Keep my edits' }));
  expect(screen.getByLabelText('Prompt text')).toHaveValue('Write an API reference for partners.');
  await select('Prompt', 'Runbook · service');
  fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
  await waitFor(() => expect(screen.getByLabelText('Prompt text')).toHaveValue('Write a runbook.'));
  await select('Prompt', 'Write my own');
  expect(screen.getByLabelText('Prompt text')).toHaveValue('');
});

test('without a native model Generate stays disabled; a refused request shows its message', async () => {
  setup({ models: [MODELS[1]] });
  await openContext();
  expect(await screen.findByText('Add a model in Models first.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
});

test('with no model the reason is visible without opening Agent context', async () => {
  setup({ models: [MODELS[1]] });
  await screen.findByRole('button', { name: 'Agent context' });
  expect(shown('Add a model in Models first.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
});

test('a failed context refresh is visible without opening Agent context', async () => {
  setup({ contextFails: true });
  await screen.findByRole('checkbox', { name: 'Code' });
  check('Code'); check('Payments API');
  await waitFor(() => expect(screen.getAllByText('Context service is down').length).toBeGreaterThan(0));
  expect(shown('Context service is down')).toBeVisible();
});

test('a context longer than 100,000 characters disables Generate and says why', async () => {
  setup();
  await openContext();
  fireEvent.change(screen.getByLabelText('Prompt text'), { target: { value: 'Describe it.' } });
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Doc' } });
  check('Code'); check('Payments API');
  await waitFor(() => expect(screen.getByLabelText('Context')).toHaveValue('# What you are documenting\n\nrepo'));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Context'), { target: { value: 'x'.repeat(100001) } });
  const message = 'The context is longer than 100,000 characters. Shorten it before you generate.';
  expect(shown(message)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Agent context' }));
  expect(shown(message)).toBeVisible();
});

test('a refused request shows its message and keeps the form', async () => {
  setup({ error: 'Server prod-1 is not available: it was removed, archived or disabled.' });
  await screen.findByRole('checkbox', { name: 'Servers' }); check('Servers'); check('prod-1 (10.0.0.5)');
  fireEvent.change(screen.getByLabelText('Prompt text'), { target: { value: 'Describe the host.' } });
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'prod-1' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText(/Server prod-1 is not available/)).toBeVisible();
  expect(screen.getByLabelText('Document name')).toHaveValue('prod-1');
});

test('Generate is disabled while the context is updating', async () => {
  const { held, contextText } = setup({ hold: true });
  fireEvent.change(await screen.findByLabelText('Prompt text'), { target: { value: 'Describe it.' } });
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Doc' } });
  check('Code'); check('Payments API');
  await waitFor(() => expect(held).toHaveLength(1));
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  held[0].resolve(Response.json({ text: contextText(held[0].body) }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled());
});

test('a failed prompt load keeps the previous choice and text', async () => {
  setup({ failPrompt: 'p2' });
  await select('Prompt', 'API reference · code');
  await waitFor(() => expect(screen.getByLabelText('Prompt text')).toHaveValue('Write an API reference.'));
  await select('Prompt', 'Runbook · service');
  expect(await screen.findByText('Prompt is gone')).toBeVisible();
  expect(screen.getByLabelText('Prompt text')).toHaveValue('Write an API reference.');
  expect(screen.getByLabelText('Prompt')).toHaveTextContent('API reference · code');
});
