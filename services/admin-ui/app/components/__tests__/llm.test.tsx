import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import LlmSettingsView from '../LlmSettingsView';
import LlmModelForm from '../LlmModelForm';
import type { ModelProfile } from '../../lib/llm';

afterEach(() => vi.unstubAllGlobals());
const bedrock: ModelProfile = { id: 'bedrock-1', name: 'Bedrock Claude', provider: 'bedrock', model: 'eu.anthropic.claude-test-v1:0', api_format: 'native', base_url: null, aws_region: 'eu-central-1', enabled: true, has_api_key: true, revision: 1, created_at: '', updated_at: '' };

test('editing a Bedrock model preserves its saved key and supports disabling', async () => {
  const saved = vi.fn();
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify({ ...bedrock, ...JSON.parse(init!.body as string), revision: 2 })));
  vi.stubGlobal('fetch', fetch);
  render(<LlmModelForm model={bedrock} onSaved={saved} onClose={() => {}} />);
  expect(screen.getByLabelText('AWS region')).toHaveValue('eu-central-1');
  expect(screen.getByLabelText('Bedrock API key')).toHaveValue('');
  expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('switch', { name: 'Enabled' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  const body = JSON.parse(fetch.mock.calls[0][1]!.body as string);
  expect(body).toMatchObject({ provider: 'bedrock', aws_region: 'eu-central-1', base_url: null, enabled: false, revision: 1 });
  expect(body).not.toHaveProperty('api_key');
});

test('a stale model edit keeps the new key and other unsaved values', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { message: 'This model changed. Cancel and reopen it before saving.' } }), { status: 409 })));
  render(<LlmModelForm model={bedrock} onSaved={() => {}} onClose={() => {}} />);
  fireEvent.change(screen.getByLabelText('Bedrock API key'), { target: { value: 'replacement-key' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await screen.findByRole('alert');
  expect(screen.getByLabelText('Bedrock API key')).toHaveValue('replacement-key');
});

test('connection test is explicit and uses the saved revision', async () => {
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method
    ? { ok: true, message: 'Model responded successfully.' }
    : { models: [bedrock], assignments: { search_model_id: null, code_model_id: null, revision: 1 } })));
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><LlmSettingsView /></MemoryRouter>);
  const button = await screen.findByRole('button', { name: 'Test connection' });
  expect(fetch.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0);
  fireEvent.click(button);
  await screen.findByText('Bedrock Claude: Model responded successfully.');
  const call = fetch.mock.calls.find(([, init]) => init?.method)!;
  expect(call[0]).toBe('/api/llm/models/bedrock-1/test');
  expect(JSON.parse(call[1]!.body as string)).toEqual({ revision: 1 });
});

test('model settings manages connections and links to Agents for model selection', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ models: [bedrock], assignments: { revision: 1 } }))));
  render(<MemoryRouter><LlmSettingsView /></MemoryRouter>);
  expect(await screen.findByRole('cell', { name: 'Bedrock Claude' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Agents page' })).toHaveAttribute('href', '/agents');
  expect(screen.queryByText('Agent models')).not.toBeInTheDocument();
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
});


test('creating an embedding connection includes its dimensions and version', async () => {
  const user = userEvent.setup();
  const saved = vi.fn();
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify({ ...JSON.parse(init!.body as string), id: 'embed', revision: 1 })));
  vi.stubGlobal('fetch', fetch);
  render(<LlmModelForm model={null} onSaved={saved} onClose={() => {}} />);
  await user.click(screen.getByRole('combobox', { name: 'Model type' }));
  await user.click(screen.getByRole('option', { name: 'Embeddings' }));
  expect(screen.getByRole('combobox', { name: 'Provider' })).toBeEnabled();
  expect(screen.queryByLabelText('API format')).not.toBeInTheDocument();
  await user.type(screen.getByLabelText('Connection name'), 'Document embeddings');
  await user.type(screen.getByLabelText('Model ID'), 'text-embedding-test');
  fireEvent.change(screen.getByLabelText('Embedding dimensions'), { target: { value: '1536' } });
  await user.click(screen.getByRole('button', { name: 'Save model' }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toMatchObject({ api_format: 'embeddings', provider: 'openai', embedding_dimensions: 1536, embedding_version: '1' });
});


test('Bedrock embedding form keeps embedding type and saves its region', async () => {
  const user = userEvent.setup();
  const saved = vi.fn();
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify({ ...JSON.parse(init!.body as string), id: 'embed', revision: 1 })));
  vi.stubGlobal('fetch', fetch);
  render(<LlmModelForm model={null} onSaved={saved} onClose={() => {}} />);
  await user.click(screen.getByRole('combobox', { name: 'Model type' }));
  await user.click(screen.getByRole('option', { name: 'Embeddings' }));
  await user.click(screen.getByRole('combobox', { name: 'Provider' }));
  expect(screen.queryByRole('option', { name: 'Anthropic' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('option', { name: 'AWS Bedrock' }));
  expect(screen.getByRole('combobox', { name: 'Model type' })).toHaveTextContent('Embeddings');
  expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument();
  await user.type(screen.getByLabelText('Connection name'), 'Titan embeddings');
  await user.type(screen.getByLabelText('Model ID'), 'amazon.titan-embed-text-v2:0');
  await user.type(screen.getByLabelText('AWS region'), 'eu-central-1');
  await user.type(screen.getByLabelText('Bedrock API key'), 'test-key');
  await user.click(screen.getByRole('button', { name: 'Save model' }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toMatchObject({ api_format: 'embeddings', provider: 'bedrock', base_url: null, aws_region: 'eu-central-1', embedding_dimensions: 1024, api_key: 'test-key' });
});
