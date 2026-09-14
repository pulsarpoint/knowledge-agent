import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import AgentModelSelection from '../AgentModelSelection';
import type { ModelProfile } from '../../lib/llm';

const bedrock: ModelProfile = { id: 'bedrock', name: 'Bedrock Claude', provider: 'bedrock', model: 'eu.anthropic.claude-test-v1:0', api_format: 'native', base_url: null, aws_region: 'eu-central-1', enabled: true, has_api_key: true, revision: 1, created_at: '', updated_at: '' };
const responses = { ...bedrock, id: 'responses', name: 'Responses model', provider: 'openai' as const, api_format: 'responses' as const };
const models = [bedrock, responses, { ...responses, id: 'embed', name: 'Embedding model', api_format: 'embeddings' as const }, { ...responses, id: 'chat', name: 'Chat model', api_format: 'chat_completions' as const }, { ...responses, id: 'disabled', name: 'Disabled model', enabled: false }];
const assignments = { search_model_id: 'bedrock', code_model_id: 'bedrock', operations_model_id: null, revision: 1, updated_at: '2026-09-10T10:00:00Z' };
afterEach(() => vi.unstubAllGlobals());

test.each(['search', 'code', 'operations'] as const)('%s saves only its own model and offers compatible enabled connections', async agent => {
  const onSaved = vi.fn();
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method
    ? { ...assignments, ...JSON.parse(init.body as string), revision: 2 }
    : { models, assignments })));
  vi.stubGlobal('fetch', fetch);
  const user = userEvent.setup();
  render(<MemoryRouter><AgentModelSelection agent={agent} onSaved={onSaved} /></MemoryRouter>);
  await user.click(await screen.findByRole('combobox', { name: 'Model connection' }));
  expect(screen.queryByRole('option', { name: 'Disabled model' })).not.toBeInTheDocument();
  expect(screen.queryByRole('option', { name: 'Embedding model' })).not.toBeInTheDocument();
  expect(screen.queryByRole('option', { name: 'Chat model' }) !== null).toBe(agent === 'search');
  await user.click(screen.getByRole('option', { name: 'Responses model' }));
  expect(fetch.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0);
  await user.click(screen.getByRole('button', { name: 'Save model' }));
  expect(await screen.findByRole('status')).toHaveTextContent('model saved');
  const [url, init] = fetch.mock.calls.find(([, init]) => init?.method)!;
  expect(url).toBe('/api/llm/assignments');
  expect(JSON.parse(init!.body as string)).toEqual({ [`${agent}_model_id`]: 'responses', revision: 1 });
  expect(onSaved).toHaveBeenCalledOnce();
  expect(screen.getByRole('button', { name: 'Save model' })).toBeDisabled();
});

test('a disabled selection is visible and can be cleared without assigning a replacement', async () => {
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method
    ? { ...assignments, search_model_id: null, revision: 2 }
    : { models: [{ ...bedrock, enabled: false }], assignments })));
  vi.stubGlobal('fetch', fetch);
  const user = userEvent.setup();
  render(<MemoryRouter><AgentModelSelection agent="search" onSaved={vi.fn()} /></MemoryRouter>);
  expect(await screen.findByRole('combobox')).toHaveTextContent('Bedrock Claude (disabled)');
  await user.click(screen.getByRole('combobox'));
  await user.click(screen.getByRole('option', { name: 'Not configured' }));
  await user.click(screen.getByRole('button', { name: 'Save model' }));
  await screen.findByRole('status');
  expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method)![1]!.body as string)).toEqual({ search_model_id: null, revision: 1 });
});

test('a stale save keeps the draft and requires refreshing the shared revision', async () => {
  const onSaved = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => init?.method
    ? new Response(JSON.stringify({ error: { message: 'Assignments changed. Refresh before saving.' } }), { status: 409 })
    : new Response(JSON.stringify({ models, assignments }))));
  const user = userEvent.setup();
  render(<MemoryRouter><AgentModelSelection agent="code" onSaved={onSaved} /></MemoryRouter>);
  await user.click(await screen.findByRole('combobox'));
  await user.click(screen.getByRole('option', { name: 'Responses model' }));
  await user.click(screen.getByRole('button', { name: 'Save model' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Assignments changed');
  expect(screen.getByRole('combobox')).toHaveTextContent('Responses model');
  expect(onSaved).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Refresh models' }));
  await waitFor(() => expect(screen.getByRole('combobox')).toHaveTextContent('Bedrock Claude'));
});
