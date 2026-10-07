import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import AgentWizard from '../AgentWizard';
import * as api from '../../lib/agentWizard';
import { Toaster } from '~/components/ui/sonner';

vi.mock('../../lib/agentWizard', () => ({ getWizardOptions: vi.fn(), generateAgent: vi.fn(), createWizardAgent: vi.fn() }));
const proposal: api.AgentProposal = {
  name: 'Payments helper', description: 'Investigate payments.', when_to_use: 'Payment incidents.',
  system_prompt: 'Find evidence before answering.', instructions: 'Check exit codes.', model_id: 'model', enabled: true,
  documents_enabled: true, repositories_enabled: true, ssh_enabled: true, skills_enabled: true, mcp_enabled: true, services_enabled: true,
  all_collections: false, all_repositories: false,
  collection_ids: ['jira'], repository_ids: ['repo'], skill_ids: ['skill'], mcp_server_ids: ['mcp'], service_ids: ['service'],
  servers: [{ server_id: 'server', instructions: null, skill_ids: null, mcp_server_ids: null, connection: null }],
};
const options: api.WizardOptions = {
  default_model_id: 'model', prompt: 'Design a specialist.', schema: { type: 'object' },
  catalog: {
    models: [{ id: 'model', name: 'Claude', model: 'claude-test', provider: 'bedrock', api_format: 'native' }],
    code_projects: [{ id: 'repo', name: 'Payments', folder_paths: ['payments'], remote_url: null }],
    collections: [{ id: 'jira', name: 'jira-tickets', description: 'Jira tickets' }],
    servers: [{ id: 'server', name: 'Staging', description: '', hostname: 'staging.test', tags: [], username: 'operator', port: 22, allow_sudo: false, known_hosts: '', credential_id: 'key' }],
    credentials: [{ id: 'key', name: 'Staging key' }], skills: [{ id: 'skill', name: 'Inspection' }],
    mcp_servers: [{ id: 'mcp', name: 'Docs MCP' }], services: [{ id: 'service', name: 'Payments API' }], existing_agents: [],
  },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getWizardOptions).mockResolvedValue(structuredClone(options));
  vi.mocked(api.generateAgent).mockResolvedValue({ proposal: structuredClone(proposal) });
  vi.mocked(api.createWizardAgent).mockResolvedValue({ id: 'new-agent' });
});
async function open() {
  const user = userEvent.setup(); const created = vi.fn(); const close = vi.fn();
  render(<MemoryRouter><AgentWizard onClose={close} onCreated={created} /><Toaster /></MemoryRouter>);
  await screen.findByLabelText('What should this agent do?');
  await user.type(screen.getByLabelText('What should this agent do?'), 'Investigate payment incidents.');
  return { user, created, close };
}

test('generates a reviewable proposal, edits resources and SSH overrides, then creates once', async () => {
  const { user, created } = await open();
  await user.click(screen.getByRole('button', { name: 'Show design context' }));
  expect((screen.getByLabelText('Generation prompt, configuration schema, and resource catalog') as HTMLTextAreaElement).value).toContain('Design a specialist.');
  await user.click(screen.getByRole('button', { name: 'Generate proposal' }));
  await screen.findByLabelText('Agent name');
  expect(api.generateAgent).toHaveBeenCalledWith('Investigate payment incidents.', 'model', 'openai');
  expect(api.createWizardAgent).not.toHaveBeenCalled();
  await user.clear(screen.getByLabelText('Agent name')); await user.type(screen.getByLabelText('Agent name'), 'Reviewed agent');
  await user.click(screen.getByRole('tab', { name: 'Documents' }));
  expect(screen.getByRole('switch', { name: 'All collections' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'jira-tickets' })).toBeChecked();
  expect(screen.queryByText('Include subfolders')).toBeNull();
  await user.click(screen.getByRole('tab', { name: 'Code projects' }));
  expect(screen.getByRole('checkbox', { name: 'Payments' })).toBeChecked();
  await user.click(screen.getByRole('switch', { name: 'All code projects' }));
  await user.click(screen.getByRole('tab', { name: 'SSH' }));
  expect(screen.getByRole('checkbox', { name: 'Staging' })).toBeChecked();
  await user.click(screen.getByRole('switch', { name: 'Use agent instructions' }));
  fireEvent.change(screen.getByLabelText('Instructions for Staging'), { target: { value: 'Inspect logs only.' } });
  await user.click(screen.getByRole('switch', { name: 'Override server connection' }));
  fireEvent.change(screen.getByLabelText('SSH login user'), { target: { value: 'observer' } });
  await user.click(screen.getByRole('tab', { name: 'Prompt' }));
  fireEvent.change(screen.getByLabelText('System prompt'), { target: { value: 'Use evidence and cite sources.' } });
  await user.click(screen.getByRole('button', { name: 'Create agent' }));
  expect(api.createWizardAgent).toHaveBeenCalledTimes(1);
  expect(api.createWizardAgent).toHaveBeenCalledWith(expect.objectContaining({ name: 'Reviewed agent', runtime: 'openai', all_repositories: true, repository_ids: [], system_prompt: 'Use evidence and cite sources.', servers: [expect.objectContaining({ instructions: 'Inspect logs only.', connection: expect.objectContaining({ username: 'observer', credential_id: 'key' }) })] }));
  expect(created).toHaveBeenCalledWith('new-agent');
  expect(await screen.findByText('Agent Reviewed agent created')).toBeVisible();
});

test('generation and save errors preserve role and proposal for retry', async () => {
  const { user, created } = await open();
  vi.mocked(api.generateAgent).mockRejectedValueOnce(new Error('Model unavailable.'));
  await user.click(screen.getByRole('button', { name: 'Generate proposal' }));
  expect(await screen.findByText('Model unavailable.')).toBeVisible();
  expect(screen.getByLabelText('What should this agent do?')).toHaveValue('Investigate payment incidents.');
  await user.click(screen.getByRole('button', { name: 'Generate proposal' }));
  await screen.findByLabelText('Agent name');
  vi.mocked(api.createWizardAgent).mockRejectedValueOnce(new Error('Name already exists.'));
  await user.click(screen.getByRole('button', { name: 'Create agent' }));
  expect(await screen.findByText('Name already exists.')).toBeVisible();
  expect(screen.getByLabelText('Agent name')).toHaveValue('Payments helper');
  expect(created).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Edit role' }));
  expect(screen.getByLabelText('What should this agent do?')).toHaveValue('Investigate payment incidents.');
  await user.click(screen.getByRole('button', { name: 'Back to proposal' }));
  expect(screen.getByLabelText('Agent name')).toHaveValue('Payments helper');
});

test('requires a prompt before saving and can cancel without creating', async () => {
  const { user, close } = await open();
  await user.click(screen.getByRole('button', { name: 'Generate proposal' }));
  await screen.findByLabelText('Agent name');
  await user.click(screen.getByRole('tab', { name: 'Prompt' }));
  fireEvent.change(screen.getByLabelText('System prompt'), { target: { value: '' } });
  await user.click(screen.getByRole('tab', { name: 'General' }));
  expect(screen.getByRole('button', { name: 'Create agent' })).toBeDisabled();
  expect(api.createWizardAgent).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(close).toHaveBeenCalledTimes(1);
});


test('start blank opens an empty proposal without generating and can create without a model', async () => {
  const user = userEvent.setup(); const created = vi.fn();
  vi.mocked(api.getWizardOptions).mockResolvedValue({ ...structuredClone(options), default_model_id: null, catalog: { ...structuredClone(options.catalog), models: [] } });
  render(<MemoryRouter><AgentWizard onClose={vi.fn()} onCreated={created} /></MemoryRouter>);
  await user.click(await screen.findByRole('button', { name: 'Start blank' }));
  expect(api.generateAgent).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Agent name')).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Create agent' })).toBeDisabled();
  await user.type(screen.getByLabelText('Agent name'), 'Blank helper');
  await user.click(screen.getByRole('tab', { name: 'Prompt' }));
  fireEvent.change(screen.getByLabelText('System prompt'), { target: { value: 'Answer carefully.' } });
  await user.click(screen.getByRole('button', { name: 'Create agent' }));
  expect(api.createWizardAgent).toHaveBeenCalledWith(expect.objectContaining({ name: 'Blank helper', runtime: 'openai', system_prompt: 'Answer carefully.',
    model_id: null, documents_enabled: false, ssh_enabled: false, collection_ids: [], servers: [] }));
  expect(created).toHaveBeenCalledWith('new-agent');
});

test('the Agent type choice filters models and is sent with the agent', async () => {
  vi.mocked(api.getWizardOptions).mockResolvedValue({ ...structuredClone(options), catalog: { ...structuredClone(options.catalog), models: [
    { id: 'model', name: 'Claude', model: 'eu.anthropic.claude-test', provider: 'bedrock', api_format: 'native' },
    { id: 'gpt', name: 'GPT', model: 'gpt-5', provider: 'openai', api_format: 'responses' }] } });
  const user = userEvent.setup();
  render(<MemoryRouter><AgentWizard onClose={vi.fn()} onCreated={vi.fn()} /><Toaster /></MemoryRouter>);
  await screen.findByLabelText('What should this agent do?');
  await user.click(screen.getByRole('button', { name: 'Start blank' }));
  expect(screen.getByRole('combobox', { name: 'Agent type' })).toHaveTextContent('Native');
  await user.click(screen.getByRole('combobox', { name: 'Agent model' }));
  await user.click(screen.getByRole('option', { name: /^GPT/ }));
  await user.click(screen.getByRole('combobox', { name: 'Agent type' }));
  await user.click(screen.getByRole('option', { name: 'Claude Code' }));
  expect(screen.getByRole('combobox', { name: 'Agent model' })).toHaveTextContent('No model (choose later)');  // GPT does not fit
  await user.click(screen.getByRole('combobox', { name: 'Agent model' }));
  expect(screen.queryByRole('option', { name: /^GPT/ })).toBeNull();
  await user.click(screen.getByRole('option', { name: /^Claude/ }));
  await user.type(screen.getByLabelText('Agent name'), 'Writer');
  await user.click(screen.getByRole('tab', { name: 'Prompt' }));
  await user.type(screen.getByLabelText('System prompt'), 'Write documentation.');
  await user.click(screen.getByRole('button', { name: 'Create agent' }));
  expect(api.createWizardAgent).toHaveBeenCalledWith(expect.objectContaining({ name: 'Writer', model_id: 'model',
    runtime: 'claude_code', claude_code_tools: ['Read', 'Grep', 'Glob', 'Bash'] }));
});

test('step 1 starts with the Agent type, which shapes the proposal and a blank start', async () => {
  vi.mocked(api.getWizardOptions).mockResolvedValue({ ...structuredClone(options), default_model_id: 'gpt', catalog: { ...structuredClone(options.catalog), models: [
    { id: 'gpt', name: 'GPT', model: 'gpt-5', provider: 'openai', api_format: 'responses' },
    { id: 'model', name: 'Claude', model: 'eu.anthropic.claude-test', provider: 'bedrock', api_format: 'native' }] } });
  const { user } = await open();
  const fields = screen.getAllByRole('combobox').map(box => box.getAttribute('id'));
  expect(fields[0]).toBe('wizard-agent-type');
  expect(screen.getByRole('combobox', { name: 'Agent type' })).toHaveTextContent('Native');
  await user.click(screen.getByRole('combobox', { name: 'Agent type' }));
  await user.click(screen.getByRole('option', { name: 'Claude Code' }));
  await user.click(screen.getByRole('button', { name: 'Generate proposal' }));
  expect(api.generateAgent).toHaveBeenCalledWith('Investigate payment incidents.', 'gpt', 'claude_code');
  expect(await screen.findByRole('combobox', { name: 'Agent type' })).toHaveTextContent('Claude Code');
});

test('a blank Claude Code agent does not take a non-Claude generator model', async () => {
  vi.mocked(api.getWizardOptions).mockResolvedValue({ ...structuredClone(options), default_model_id: 'gpt', catalog: { ...structuredClone(options.catalog), models: [
    { id: 'gpt', name: 'GPT', model: 'gpt-5', provider: 'openai', api_format: 'responses' }] } });
  const user = userEvent.setup();
  render(<MemoryRouter><AgentWizard onClose={vi.fn()} onCreated={vi.fn()} /></MemoryRouter>);
  await user.click(await screen.findByRole('combobox', { name: 'Agent type' }));
  await user.click(screen.getByRole('option', { name: 'Claude Code' }));
  await user.click(screen.getByRole('button', { name: 'Start blank' }));
  expect(screen.getByRole('combobox', { name: 'Agent model' })).toHaveTextContent('No model (choose later)');
});
