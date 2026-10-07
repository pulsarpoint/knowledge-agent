import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import AgentsView from '../AgentsView';
import AgentDetailView from '../AgentDetailView';
import { Toaster } from '~/components/ui/sonner';

// The agent page is the shared page now; its heavy views are covered by their own tests.
vi.mock('../OperationsView', () => ({ default: () => null }));
vi.mock('../PromptsView', () => ({ default: () => null }));
vi.mock('../AgentInstructions', () => ({ default: () => null }));

const base = { when_to_use: '', enabled: true, is_coordinator: false, documents_enabled: false, repositories_enabled: false,
  ssh_enabled: false, skills_enabled: false, mcp_enabled: false, services_enabled: false, documents_grounded: false, revision: 1,
  all_collections: false, all_repositories: false, collection_ids: [], repository_ids: [], service_ids: [], skill_ids: [], active_prompt: null };
const writer = { ...base, id: 'writer', name: 'Technical writer', description: 'Writes documentation', runtime: 'claude_code',
  model_id: null, model_name: null, prompt_title: null, prompt_version: null };
const search = { ...base, id: 'search', name: 'Search agent', description: '', runtime: 'openai', documents_enabled: true,
  model_id: 'm', model_name: 'GPT', prompt_title: 'Search', prompt_version: 2 };
afterEach(() => vi.unstubAllGlobals());

function setup() {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url === '/api/agents') return Response.json({ agents: [search, writer] });
    if (url === '/api/claude-code/agents' && method === 'POST') return Response.json({ id: 'new-writer' }, { status: 201 });
    if (url === '/api/claude-code/agents/writer' && method === 'PUT') return Response.json({ ...writer, ...JSON.parse(init!.body as string), revision: 2 });
    if (url === '/api/llm') return Response.json({ models: [], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
    if (url.endsWith('/options')) return Response.json({ collections: [], repositories: [], services: [], skills: [] });
    if (url === '/api/agents/new-writer') return Response.json({ ...writer, id: 'new-writer', name: 'Release notes writer', description: 'Writes release notes' });
    if (url.startsWith('/api/agents/')) return Response.json({ ...writer, id: url.split('/').pop() });
    return Response.json({});
  }));
  return calls;
}
function Detail() { const { id } = useParams(); return <AgentDetailView agentId={id!} />; }
function app(path: string) {
  return <MemoryRouter initialEntries={[path]}><Routes><Route path="/agents" element={<AgentsView />} /><Route path="/agents/:id" element={<Detail />} /></Routes><Toaster /></MemoryRouter>;
}

test('All agents shows Claude Code agents with their badge and creates one from a name and description', async () => {
  const calls = setup();
  render(app('/agents'));
  const row = (await screen.findByRole('link', { name: 'Technical writer' })).closest('tr')!;
  expect(within(row).getByText('Claude Code SDK')).toBeVisible();
  expect(within(row).getByText('Read, Grep, Glob, Bash')).toBeVisible();
  expect(within(row).getByText('Chosen per session')).toBeVisible();
  expect(within(row).getByText('Built in')).toBeVisible();
  const other = screen.getByRole('link', { name: 'Search agent' }).closest('tr')!;
  expect(within(other).queryByText('Claude Code SDK')).toBeNull();
  expect(screen.queryByRole('button', { name: 'New Claude Code agent' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'New agent' }));
  expect(screen.getByRole('button', { name: 'New agent' })).toBeDisabled();
  const tabs = screen.getAllByRole('tab').map(tab => tab.textContent);
  expect(tabs).toEqual(['Generic agent', 'Claude Code']);
  expect(screen.getByRole('tab', { name: 'Generic agent' })).toHaveAttribute('aria-selected', 'true');
  fireEvent.mouseDown(screen.getByRole('tab', { name: 'Claude Code' }));
  expect(screen.getByRole('tab', { name: 'Claude Code' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.queryByLabelText(/Model/)).toBeNull();
  expect(screen.queryByLabelText(/Instructions/)).toBeNull();
  expect(screen.getByRole('button', { name: 'Create agent' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Release notes writer' } });
  fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Writes release notes' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create agent' }));
  await waitFor(() => expect(calls.find(c => c.method === 'POST')).toEqual({
    url: '/api/claude-code/agents', method: 'POST', body: { name: 'Release notes writer', description: 'Writes release notes' } }));
  expect(await screen.findByRole('heading', { name: 'Release notes writer', level: 2 })).toBeVisible();
  expect(await screen.findByText('Agent Release notes writer created')).toBeVisible();
});
