import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import AgentsView from '../AgentsView';
import AgentDetailView from '../AgentDetailView';

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
    if (url === '/api/claude-code/agents/writer' && method === 'PUT') return Response.json({ ...writer, name: 'Writer', revision: 2 });
    if (url === '/api/agents/new-writer') return Response.json({ ...writer, id: 'new-writer', name: 'Release notes writer', description: 'Writes release notes' });
    if (url.startsWith('/api/agents/')) return Response.json({ ...writer, id: url.split('/').pop() });
    return Response.json({});
  }));
  return calls;
}
function Detail() { const { id } = useParams(); return <AgentDetailView agentId={id!} />; }
function app(path: string) {
  return <MemoryRouter initialEntries={[path]}><Routes><Route path="/agents" element={<AgentsView />} /><Route path="/agents/:id" element={<Detail />} /></Routes></MemoryRouter>;
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
  expect(screen.getByRole('button', { name: 'New Agent Wizard' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'New Claude Code agent' }));
  expect(screen.queryByLabelText(/Model/)).toBeNull();
  expect(screen.queryByLabelText(/Instructions/)).toBeNull();
  expect(screen.getByRole('button', { name: 'Create agent' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Release notes writer' } });
  fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Writes release notes' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create agent' }));
  await waitFor(() => expect(calls.find(c => c.method === 'POST')).toEqual({
    url: '/api/claude-code/agents', method: 'POST', body: { name: 'Release notes writer', description: 'Writes release notes' } }));
  expect(await screen.findByRole('heading', { name: 'Release notes writer', level: 2 })).toBeVisible();
});

test('a Claude Code agent page edits only its name and description', async () => {
  const calls = setup();
  render(app('/agents/writer'));
  expect(await screen.findByRole('heading', { name: 'Technical writer', level: 2 })).toBeVisible();
  expect(screen.getByText('Claude Code SDK')).toBeVisible();
  expect(screen.queryByRole('tab')).toBeNull();
  expect(screen.queryByRole('combobox', { name: 'Model connection' })).toBeNull();
  expect(screen.getByText(/are chosen when a session starts/)).toBeVisible();
  expect(screen.getByRole('link', { name: /With an agent/ })).toHaveAttribute('href', '/documentation/generations/new?scope=agent');
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Writer' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save agent' }));
  await waitFor(() => expect(calls.find(c => c.method === 'PUT')).toEqual({
    url: '/api/claude-code/agents/writer', method: 'PUT', body: { name: 'Writer', description: 'Writes documentation', revision: 1 } }));
  expect(await screen.findByRole('heading', { name: 'Writer', level: 2 })).toBeVisible();
});
