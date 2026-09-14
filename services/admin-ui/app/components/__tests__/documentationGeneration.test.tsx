import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import DocumentationGenerationForm from '../DocumentationGenerationForm';

const services = [{ id: 'app', name: 'Payments', service_type: 'custom', repositories: [{ id: 'repo', name: 'Application code', archived: false }] }, { id: 'db', name: 'Postgres', service_type: 'builtin', repositories: [] }];
const prompts = [
  { id: 'code-prompt', name: 'Code analysis and architecture', input_kind: 'code' },
  { id: 'server-prompt', name: 'Deployment and service operations', input_kind: 'server' },
  { id: 'environment-prompt', name: 'Environment architecture', input_kind: 'mixed' },
  { id: 'infrastructure-prompt', name: 'Server infrastructure architecture', input_kind: 'server' },
  { id: 'diagram-prompt', name: 'Code component diagram (D2 / TALA)', input_kind: 'code' },
].map(p => ({ ...p, enabled: true, active_version_id: p.id + '-v1' }));
afterEach(() => vi.unstubAllGlobals());
function setup() {
  const queued = vi.fn();
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const prompt = prompts.find(p => url === '/api/documentation/prompts/' + p.id);
    const data = url === '/api/services' ? { services }
      : url === '/api/environments' ? { environments: [{ id: 'dev', name: 'Development', services: services.map(s => ({ id: s.id })), servers: [{ id: 'host', name: 'Dev host', hostname: 'dev.internal' }] }, { id: 'outside', name: 'Other environment', services: [], servers: [] }] }
      : url === '/api/servers' ? { servers: [{ id: 'host', ssh_allowed: true, access_enabled: true, ssh_username: 'inspector' }] }
      : url === '/api/llm' ? { models: [{ id: 'model', name: 'Documentation model', provider: 'openai', api_format: 'responses', enabled: true }, { id: 'embedding', name: 'Embedding', provider: 'bedrock', api_format: 'embeddings', model: 'titan', enabled: true }] }
      : url === '/api/documentation/prompts' ? { prompts }
      : prompt ? { ...prompt, versions: [{ id: prompt.active_version_id, version: 1, content: 'Active ' + prompt.name }] } : { job_id: 'job' };
    return { ok: true, status: init?.method === 'POST' ? 202 : 200, json: async () => data };
  });
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><DocumentationGenerationForm onQueued={queued} /></MemoryRouter>);
  return { fetch, queued };
}
async function select(label: string, option: string) {
  fireEvent.click(await screen.findByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}
test('diagram is independent, uses source folders and stores only a per-run prompt override', async () => {
  const { fetch, queued } = setup();
  await select('Service', 'Payments');
  fireEvent.click(screen.getByRole('checkbox', { name: 'D2 / TALA diagram' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Application code' }));
  const prompt = await screen.findByLabelText('Prompt for this run · active version 1');
  expect(prompt).toHaveValue('Active Code component diagram (D2 / TALA)');
  fireEvent.change(prompt, { target: { value: 'Focus on queue consumers' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  const request = fetch.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(request[1]!.body as string)).toMatchObject({ scope: 'service', model_id: 'model', service_id: 'app', environment_id: null, code_project_ids: ['repo'], server_ids: [], units: [{ kind: 'diagram', prompt_id: 'diagram-prompt', prompt_version_id: 'diagram-prompt-v1', prompt_override: 'Focus on queue consumers' }] });
  expect(fetch.mock.calls.some(([url]) => url.startsWith('/api/agents'))).toBe(false);
  expect(fetch.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);
});
test('built-in service requires an environment and selected server for deployment', async () => {
  const { fetch } = setup();
  await select('Service', 'Postgres');
  expect(screen.getByRole('checkbox', { name: 'Code analysis' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Deployment on an environment' }));
  expect(screen.getByRole('button', { name: 'Generate documents' })).toBeDisabled();
  await select('Environment', 'Development');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Dev host · dev.internal' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate documents' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
  expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string)).toMatchObject({ environment_id: 'dev', server_ids: ['host'], code_project_ids: [], units: [{ kind: 'deployment', prompt_id: 'server-prompt', prompt_version_id: 'server-prompt-v1' }] });
});
test('whole environment clears service selections and uses its own editable active prompt', async () => {
  const { fetch, queued } = setup();
  await select('Service', 'Payments');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Code analysis' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Application code' }));
  await select('Documentation for', 'Whole environment');
  expect(screen.queryByLabelText('Service')).not.toBeInTheDocument();
  await select('Environment', 'Development');
  const prompt = await screen.findByLabelText('Prompt for this run · active version 1');
  expect(prompt).toHaveValue('Active Environment architecture');
  fireEvent.change(prompt, { target: { value: 'Custom' } });
  fireEvent.click(screen.getByRole('button', { name: 'Reset to active prompt' }));
  expect(prompt).toHaveValue('Active Environment architecture');
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string)).toMatchObject({ scope: 'environment', service_id: null, environment_id: 'dev', code_project_ids: [], server_ids: [], units: [{ kind: 'architecture', prompt_id: 'environment-prompt' }] });
});

test('environment overview and infrastructure require separate selections and prompts', async () => {
  const { fetch, queued } = setup();
  await select('Documentation for', 'Whole environment');
  await select('Environment', 'Development');
  expect(screen.getByRole('checkbox', { name: 'Full environment architecture' })).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Server infrastructure architecture' }));
  expect(screen.getByRole('button', { name: 'Generate documents' })).toBeDisabled();
  await select('Infrastructure server', 'Dev host · dev.internal');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Generate documents' })).toBeEnabled());
  expect(screen.getAllByRole('textbox', { name: 'Prompt for this run · active version 1' })).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string)).toMatchObject({
    scope: 'environment', service_id: null, environment_id: 'dev', server_ids: ['host'], code_project_ids: [],
    units: [{ kind: 'architecture', prompt_id: 'environment-prompt' }, { kind: 'infrastructure', prompt_id: 'infrastructure-prompt' }],
  });
});

test('infrastructure can run alone and changing environments clears its server selection', async () => {
  const { fetch, queued } = setup();
  await select('Documentation for', 'Whole environment');
  await select('Environment', 'Development');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Full environment architecture' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Server infrastructure architecture' }));
  await select('Infrastructure server', 'Dev host · dev.internal');
  await select('Environment', 'Other environment');
  expect(screen.getByRole('button', { name: 'Generate documents' })).toBeDisabled();
  expect(screen.getByLabelText('Infrastructure server')).toHaveTextContent('Select…');
  await select('Environment', 'Development');
  expect(screen.getByRole('button', { name: 'Generate documents' })).toBeDisabled();
  await select('Infrastructure server', 'Dev host · dev.internal');
  const prompt = await screen.findByRole('textbox', { name: 'Prompt for this run · active version 1' });
  expect(prompt).toHaveValue('Active Server infrastructure architecture');
  fireEvent.change(prompt, { target: { value: 'Explain storage and ingress' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate documents' }));
  await waitFor(() => expect(queued).toHaveBeenCalledOnce());
  expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body as string).units).toEqual([
    { kind: 'infrastructure', prompt_id: 'infrastructure-prompt', prompt_version_id: 'infrastructure-prompt-v1', prompt_override: 'Explain storage and ingress' },
  ]);
});
