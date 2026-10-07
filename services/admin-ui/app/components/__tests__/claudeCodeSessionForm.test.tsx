import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import { NewGeneration } from '../ClaudeCodeSessionForm';

afterEach(() => vi.unstubAllGlobals());
const NO_FOLDER = 'Claude Code needs at least one working folder: choose code projects, or environments or services with code projects, or give the agent code projects.';
const bodies: Record<string, unknown> = {
  '/api/agents': { agents: [
    { id: 'api-writer', name: 'API writer', runtime: 'claude_code', enabled: true, is_coordinator: false, model_id: 'claude', model_name: 'Claude on Bedrock' },
    { id: 'writer', name: 'Technical writer', runtime: 'claude_code', enabled: true, is_coordinator: false, model_id: 'claude', model_name: 'Claude on Bedrock' },
    { id: 'no-model', name: 'Unfinished writer', runtime: 'claude_code', enabled: true, is_coordinator: false, model_id: null, model_name: null },
    { id: 'retired', name: 'Retired writer', runtime: 'claude_code', enabled: false, is_coordinator: false, model_id: 'claude', model_name: 'Claude on Bedrock' },
    { id: 'search', name: 'Search agent', runtime: 'openai', enabled: true, is_coordinator: false, model_id: 'gpt', model_name: 'GPT' },
    { id: 'coordinator', name: 'Coordinator', runtime: 'openai', enabled: true, is_coordinator: true, model_id: 'gpt', model_name: 'GPT' }] },
  '/api/code-projects': { projects: [{ id: 'repo', name: 'Payments API' }, { id: 'worker', name: 'Payments worker' }] },
  '/api/environments': { environments: [{ id: 'env', name: 'Production', services: [], servers: [] }] },
  '/api/services': { services: [{ id: 'svc', name: 'Payments' }] },
  '/api/servers': { servers: [{ id: 'srv', name: 'prod-1', hostname: '10.0.0.5', description: 'Payments host' }] },
  '/api/documentation/folders': { generated_folder: 'generated-documents', folders: [] },
};

function setup(error?: string, path = '/documentation/generations/new') {
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/claude-code/sessions' && init?.method === 'POST') {
      posts.push(JSON.parse(init.body as string));
      return error ? Response.json({ error: { message: error } }, { status: 400 }) : Response.json({ id: 'session-1' }, { status: 201 });
    }
    return Response.json(bodies[url] ?? {});
  }));
  render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/documentation/generations/new" element={<NewGeneration />} />
    <Route path="/documentation/generations/:sessionId" element={<div>Session page</div>} />
  </Routes></MemoryRouter>);
  return posts;
}
const check = (name: string) => fireEvent.click(screen.getByRole('checkbox', { name }));
async function select(label: string, option: string) {
  fireEvent.click(await screen.findByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}

test('Generate starts a session from an agent, what to document and instructions, and opens it', async () => {
  const posts = setup();
  fireEvent.click(await screen.findByLabelText('Agent'));
  expect(await screen.findByRole('option', { name: 'Technical writer (Claude Code)' })).toBeVisible();
  expect(screen.getByRole('option', { name: 'Search agent (Native)' })).toBeVisible();  // both agent types
  for (const hidden of [/Retired writer/, /Unfinished writer/, /Coordinator/]) expect(screen.queryByRole('option', { name: hidden })).toBeNull();
  fireEvent.click(screen.getByRole('option', { name: 'Technical writer (Claude Code)' }));
  expect(screen.getByText(/own model, prompt, instructions, skills and tools \(Claude on Bedrock\)/)).toBeVisible();
  for (const removed of ['Model', 'Prompt', 'Notes', 'Comments']) expect(screen.queryByLabelText(removed)).toBeNull();
  expect(screen.queryByRole('button', { name: 'Choose a folder' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments – API reference' } });
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();  // nothing to document yet
  check('Code');
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();  // a choice without items is not enough
  check('Payments API');
  expect(screen.getByLabelText('Documentation folder')).toHaveValue('payments-api-docs');
  check('Environment'); check('Production');
  check('Services'); check('Payments');
  check('Servers'); check('prod-1 (10.0.0.5)');
  fireEvent.change(screen.getByLabelText('Instructions'), { target: { value: 'Focus on the public API.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([{ agent_id: 'writer', code_project_ids: ['repo'], environment_ids: ['env'], service_ids: ['svc'],
    server_ids: ['srv'], instructions: 'Focus on the public API.', document_name: 'Payments – API reference', folder: 'payments-api-docs' }]);
});

test('an unchecked choice sends none of its items; a bad folder blocks Generate; refusals are shown', async () => {
  const posts = setup(NO_FOLDER);
  await select('Agent', 'Technical writer (Claude Code)');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Hosts' } });
  check('Code'); check('Payments API'); check('Code');  // unchecked again: the project is not sent
  expect(screen.queryByRole('checkbox', { name: 'Payments API' })).toBeNull();
  check('Servers'); check('prod-1 (10.0.0.5)');
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: 'Hosts' } });
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: 'hosts' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText(NO_FOLDER)).toBeVisible();
  expect(posts).toEqual([{ agent_id: 'writer', code_project_ids: [], environment_ids: [], service_ids: [], server_ids: ['srv'],
    instructions: '', document_name: 'Hosts', folder: 'hosts' }]);
});

test('the Test tab link preselects the agent; one without a model is named with the reason', async () => {
  setup(undefined, '/documentation/generations/new?agent=writer');
  expect(await screen.findByLabelText('Agent')).toHaveTextContent('Technical writer (Claude Code)');
});

test('an agent without a model is not offered; its Test tab link says why', async () => {
  setup(undefined, '/documentation/generations/new?agent=no-model');
  expect(await screen.findByText('Unfinished writer has no model. Choose one on its General tab.')).toBeVisible();
  expect(screen.getByLabelText('Agent')).toHaveTextContent('Select…');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments' } });
  check('Code'); check('Payments API');
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  await select('Agent', 'API writer (Claude Code)');
  expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled();
});

test('Generate starts a session for a native agent', async () => {
  const posts = setup();
  await select('Agent', 'Search agent (Native)');
  expect(screen.getByText(/own model, prompt, instructions, skills and tools \(GPT\)/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Hosts' } });
  check('Servers'); check('prod-1 (10.0.0.5)');
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([{ agent_id: 'search', code_project_ids: [], environment_ids: [], service_ids: [], server_ids: ['srv'],
    instructions: '', document_name: 'Hosts', folder: 'docs' }]);
});

test('an unknown agent in the link is ignored', async () => {
  setup(undefined, '/documentation/generations/new?agent=missing');
  expect(await screen.findByLabelText('Agent')).toHaveTextContent('Select…');
});
