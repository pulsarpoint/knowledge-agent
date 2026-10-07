import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import { NewDocumentationWorkflow } from '../DocumentationWorkflowView';

afterEach(() => vi.unstubAllGlobals());
const bodies: Record<string, unknown> = {
  '/api/services': { services: [] }, '/api/environments': { environments: [] },
  '/api/llm': { models: [
    { id: 'claude', name: 'Claude on Bedrock', provider: 'bedrock', model: 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0', api_format: 'native', enabled: true },
    { id: 'gpt', name: 'GPT', provider: 'openai', model: 'gpt-5', api_format: 'responses', enabled: true }],
    assignments: { search_model_id: null, code_model_id: null, revision: 1 } },
  '/api/agents': { agents: [{ id: 'api-writer', name: 'API writer', runtime: 'claude_code', enabled: true, model_id: 'claude' },
    { id: 'writer', name: 'Technical writer', runtime: 'claude_code', enabled: true },
    { id: 'retired', name: 'Retired writer', runtime: 'claude_code', enabled: false },
    { id: 'search', name: 'Search agent', runtime: 'openai', enabled: true }] },
  '/api/documentation/prompts': { prompts: [
    { id: 'code', name: 'Code analysis and architecture', input_kind: 'code', enabled: true, active_version_id: 'code-v1' },
    { id: 'ops', name: 'Deployment and service operations', input_kind: 'server', enabled: true, active_version_id: 'ops-v1' }] },
  '/api/code-projects': { projects: [{ id: 'repo', name: 'Payments API' }] },
  '/api/servers': { servers: [{ id: 'srv', name: 'prod-1', hostname: '10.0.0.5', description: 'Payments host' }] },
  '/api/documentation/folders': { generated_folder: 'generated-documents', folders: [] },
};

function setup(error?: string, path = '/documentation/generations/new?scope=agent') {
  const posts: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/claude-code/sessions' && init?.method === 'POST') {
      posts.push(JSON.parse(init.body as string));
      return error ? Response.json({ error: { message: error } }, { status: 400 }) : Response.json({ id: 'session-1' }, { status: 201 });
    }
    if (url.startsWith('/api/rag/folders')) return Response.json({ path: null, folders: [{ name: 'jira', path: 'jira' }], truncated: false });
    return Response.json(bodies[url] ?? {});
  }));
  render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/documentation/generations/new" element={<NewDocumentationWorkflow />} />
    <Route path="/documentation/sessions/:sessionId" element={<div>Session page</div>} />
  </Routes></MemoryRouter>);
  return posts;
}
async function select(label: string, option: string) {
  fireEvent.click(await screen.findByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}
async function fill() {
  fireEvent.click(await screen.findByLabelText('Agent'));
  expect(await screen.findByRole('option', { name: 'Technical writer' })).toBeVisible();
  expect(screen.queryByRole('option', { name: 'Retired writer' })).toBeNull();  // a disabled agent is not offered
  fireEvent.click(screen.getByRole('option', { name: 'Technical writer' }));
  fireEvent.click(screen.getByLabelText('Model'));
  expect(screen.queryByRole('option', { name: 'GPT' })).toBeNull();  // Claude connections only
  fireEvent.click(screen.getByRole('option', { name: 'Claude on Bedrock' }));
  await select('Prompt', 'Deployment and service operations');  // any active documentation prompt
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments – API reference' } });
}

test('With an agent starts a session with the model, prompt and context and opens it', async () => {
  const posts = setup();
  await fill();
  expect(screen.queryByRole('button', { name: 'Create workflow' })).toBeNull();
  expect(screen.getByLabelText('Documentation folder')).toHaveValue('docs');
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();  // no context yet
  fireEvent.click(screen.getByRole('checkbox', { name: 'Payments API' }));
  expect(screen.getByLabelText('Documentation folder')).toHaveValue('payments-api-docs');
  fireEvent.click(screen.getByRole('button', { name: 'Choose a folder' }));
  fireEvent.click(await screen.findByRole('button', { name: 'jira' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add folder' }));
  expect(screen.getByRole('button', { name: 'Remove jira' })).toBeVisible();
  fireEvent.click(screen.getByRole('checkbox', { name: 'prod-1 (10.0.0.5)' }));
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Deployed with Helm.' } });
  fireEvent.change(screen.getByLabelText('Comments'), { target: { value: 'Focus on the public API.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([{ agent_id: 'writer', model_id: 'claude', prompt_id: 'ops', prompt_version_id: 'ops-v1',
    comments: 'Focus on the public API.', code_project_ids: ['repo'], folders: ['jira'], server_ids: ['srv'],
    notes: 'Deployed with Helm.', document_name: 'Payments – API reference', folder: 'payments-api-docs' }]);
});

test('a bad folder name blocks Generate and server refusals are shown', async () => {
  setup('The folder /work/data/jira does not exist. Remove it from the context.');
  await fill();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: 'Payments' } });
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: 'payments' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('The folder /work/data/jira does not exist. Remove it from the context.')).toBeVisible();
});

test('opening it from an agent’s Test tab preselects the agent and its model', async () => {
  const posts = setup(undefined, '/documentation/generations/new?scope=agent&agent=api-writer');
  expect(await screen.findByLabelText('Agent')).toHaveTextContent('API writer');
  expect(screen.getByLabelText('Model')).toHaveTextContent('Claude on Bedrock');
  await select('Prompt', 'Deployment and service operations');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Session page')).toBeVisible();
  expect(posts).toEqual([expect.objectContaining({ agent_id: 'api-writer', model_id: 'claude' })]);
});

test('choosing an agent selects its model; an unknown agent in the link is ignored', async () => {
  setup(undefined, '/documentation/generations/new?scope=agent&agent=missing');
  expect(await screen.findByLabelText('Agent')).toHaveTextContent('Select…');
  await select('Agent', 'API writer');
  expect(screen.getByLabelText('Model')).toHaveTextContent('Claude on Bedrock');
});
