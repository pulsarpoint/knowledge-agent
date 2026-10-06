import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
vi.mock('../CodeGraphExplorer', () => ({ default: () => <div>Graph inspection</div> }));
import { DocumentationWorkflowsView, NewDocumentationWorkflow } from '../DocumentationWorkflowView';
import { FOLDER_RULE, SAME_DOCUMENT_NAME } from '~/lib/documents';

const prompts = [
  { id: 'code-prompt', name: 'Code analysis and architecture', input_kind: 'code' },
  { id: 'mixed-prompt', name: 'Environment architecture', input_kind: 'mixed' },
  { id: 'server-prompt', name: 'Deployment and service operations', input_kind: 'server' },
].map(p => ({ ...p, enabled: true, active_version_id: p.id + '-v1' }));
afterEach(() => vi.unstubAllGlobals());

function setup({ graphReady = false, sameName = false } = {}) {
  const posts: Record<string, unknown>[] = [];
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const json = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body });
    if (url === '/api/code-projects/repo/process' && init?.method === 'POST') { graphReady = true; return json(202, { job_id: 'graph' }); }
    if (url === '/api/documentation/generations' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string);
      posts.push(body);
      return sameName && !body.update_existing ? json(409, { error: { message: SAME_DOCUMENT_NAME } }) : json(202, { job_id: 'job' });
    }
    const prompt = prompts.find(p => url === '/api/documentation/prompts/' + p.id);
    if (prompt) return json(200, { ...prompt, versions: [{ id: prompt.active_version_id, version: 3, content: 'Active ' + prompt.name }] });
    const bodies: Record<string, unknown> = {
      '/api/services': { services: [] }, '/api/environments': { environments: [] },
      '/api/llm': { models: [{ id: 'model', name: 'Documentation model', provider: 'openai', api_format: 'responses', enabled: true }] },
      '/api/code-projects': { projects: [{ id: 'repo', name: 'Payments API', code_index: graphReady
        ? { source_hash: 'graph-hash', status: 'ready', stats: { nodeCount: 2, edgeCount: 1 }, unresolved_references: 0 } : null }] },
      '/api/documentation/prompts': { prompts },
      '/api/documentation/folders': { generated_folder: 'generated-documents', folders: [
        { folder: 'payments', source_path: 'generated-documents/payments', document_count: 1, updated_at: '2026-10-07T10:00:00Z',
          last_generated_at: null, code_projects: ['Payments API'] }] },
    };
    return json(200, bodies[url] ?? { generations: [], workflows: [] });
  });
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter initialEntries={['/documentation/generations/new']}><Routes>
    <Route path="/documentation/generations/new" element={<NewDocumentationWorkflow />} />
    <Route path="/documentation/generations" element={<div>Generation history page</div>} />
  </Routes></MemoryRouter>);
  return { fetch, posts };
}
async function select(label: string, option: string) {
  fireEvent.click(await screen.findByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}

test('code project is a third choice that generates one document into a folder after Phase 1', async () => {
  // The folder defaults to the project's slug plus -docs and stays editable.
  const { posts } = setup();
  await select('Scope', 'Code project');
  expect(screen.queryByRole('button', { name: 'Create workflow' })).toBeNull();
  await select('Code project', 'Payments API');
  expect(screen.getByLabelText('Documentation folder')).toHaveValue('payments-api-docs');
  await select('Prompt', 'Code analysis and architecture');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments – API reference' } });
  fireEvent.change(screen.getByLabelText('Comments'), { target: { value: 'Focus on refunds.' } });
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: 'team/payments' } });
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Generate graph' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Generation history page')).toBeInTheDocument();
  expect(posts).toEqual([{ scope: 'code_project', model_id: 'model', code_project_id: 'repo', prompt_id: 'code-prompt',
    prompt_version_id: 'code-prompt-v1', code_graph_hashes: { repo: 'graph-hash' }, comments: 'Focus on refunds.',
    document_name: 'Payments – API reference', folder: 'team/payments', update_existing: false }]);
});

test('server-only prompts are hidden because the run has no server access', async () => {
  setup({ graphReady: true });
  await select('Scope', 'Code project');
  fireEvent.click(await screen.findByLabelText('Prompt'));
  expect(await screen.findByRole('option', { name: 'Code analysis and architecture' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Environment architecture' })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: 'Deployment and service operations' })).toBeNull();
});

test('a bad folder names the rule and keeps Generate disabled', async () => {
  setup({ graphReady: true });
  await select('Scope', 'Code project');
  await select('Code project', 'Payments API');
  await select('Prompt', 'Code analysis and architecture');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'API reference' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Documentation folder'), { target: { value: '../payments' } });
  expect(screen.getByText(FOLDER_RULE)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
});

test('the same name in the same folder asks before saving a new version', async () => {
  const { posts } = setup({ graphReady: true, sameName: true });
  await select('Scope', 'Code project');
  await select('Code project', 'Payments API');
  await select('Prompt', 'Code analysis and architecture');
  fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Payments – API reference' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByText('Save a new version?')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Save new version' }));
  expect(await screen.findByText('Generation history page')).toBeInTheDocument();
  expect(posts.map(p => p.update_existing)).toEqual([false, true]);
  expect(posts[1]).toMatchObject({ folder: 'payments-api-docs', document_name: 'Payments – API reference' });
});

test('the generate documents page lists code project runs with their document name and folder', async () => {
  const fetch = vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => url.startsWith('/api/documentation/workflows')
    ? { workflows: [] }
    : { generations: [{ job_id: 'job', scope: 'code_project', document_name: 'Payments – API reference', folder: 'team/payments',
        mode: 'create', instructions: 'Focus on refunds.', status: 'succeeded', error: null, created_at: '2026-10-07T10:00:00Z',
        document_count: 1, token_usage: [] }] } }));
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><DocumentationWorkflowsView /></MemoryRouter>);
  expect(await screen.findByText('Payments – API reference · team/payments')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Code project documents' })).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => String(url).includes('scope=code_project'))).toBe(true);
});
