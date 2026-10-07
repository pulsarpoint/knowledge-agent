import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import AgentDetailView from '../AgentDetailView';
import AgentsView from '../AgentsView';
import * as profiles from '../../lib/profiles';
import * as llm from '../../lib/llm';
import { Toaster } from '~/components/ui/sonner';

vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgent: vi.fn(), getAgents: vi.fn(),
  getAgentOptions: vi.fn(), saveAgent: vi.fn(), saveProfileModel: vi.fn(), deleteAgent: vi.fn() }));
vi.mock('../../lib/llm', async () => ({ ...await vi.importActual('../../lib/llm'), getLlmSettings: vi.fn() }));
vi.mock('../OperationsView', () => ({ default: ({ view }: { view: string }) => <div>{view === 'test' ? 'Native test chat' : 'Operations settings'}</div> }));
vi.mock('../PromptsView', () => ({ default: () => <div>Prompt versions</div> }));
vi.mock('../AgentInstructions', () => ({ default: () => null }));
vi.mock('../AgentWizard', () => ({ default: () => <div>Agent wizard</div> }));
vi.mock('../AgentVersionsView', () => ({ default: ({ testable }: { testable?: boolean }) => <div>{testable === false ? 'Versions without Test vN' : 'Versions with Test vN'}</div> }));

const writer: profiles.AgentProfile = { id: 'writer', name: 'Technical writer', description: '', when_to_use: '', enabled: true,
  is_coordinator: false, runtime: 'claude_code', claude_code_tools: ['Read', 'Grep', 'Glob', 'Bash'], documents_enabled: false,
  repositories_enabled: false, ssh_enabled: false, skills_enabled: false, mcp_enabled: false, services_enabled: false,
  documents_grounded: false, revision: 1, model_id: 'claude', all_collections: false, all_repositories: false, collection_ids: [],
  repository_ids: [], service_ids: [], skill_ids: [], active_prompt: { id: 'p', title: 'Built-in documentation writer', version: 1 } };
const connection = { base_url: null, aws_region: 'eu-central-1', has_api_key: true, revision: 1, created_at: '', updated_at: '', enabled: true };
const claude = { ...connection, id: 'claude', name: 'Claude on Bedrock', provider: 'bedrock', model: 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0', api_format: 'native' } as llm.ModelProfile;
const gpt = { ...connection, id: 'gpt', name: 'GPT', provider: 'openai', model: 'gpt-5', api_format: 'responses' } as llm.ModelProfile;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ is_admin: true })));
  vi.mocked(profiles.getAgent).mockImplementation(async id => ({ ...structuredClone(writer), id,
    runtime: id === 'search' ? 'openai' : 'claude_code', model_id: id === 'search' ? 'gpt' : 'claude' }));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
  vi.mocked(profiles.saveAgent).mockImplementation(async value => ({ ...value, revision: value.revision + 1 }));
  vi.mocked(llm.getLlmSettings).mockResolvedValue({ models: [claude, gpt], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
});
afterEach(() => vi.unstubAllGlobals());
function Detail() { const { id } = useParams(); return <AgentDetailView key={id} agentId={id!} />; }
const open = (path: string) => render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/agents/:id" element={<Detail />} /></Routes><Toaster /></MemoryRouter>);
const TABS = ['General', 'Skills', 'Documents', 'Code projects', 'SSH', 'MCP', 'Services', 'Prompts', 'Versions', 'Test'];

test.each(['writer', 'search'])('both agent types show the same tabs: %s', async id => {
  open(`/agents/${id}`);
  await screen.findByRole('heading', { name: 'Technical writer', level: 2 });
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(TABS);
});

test('General shows the agent type, Claude models only and the Claude Code tools', async () => {
  const user = userEvent.setup();
  open('/agents/writer');
  expect(await screen.findByRole('combobox', { name: 'Agent type' })).toHaveTextContent('Claude Code');
  const tools = screen.getByRole('group', { name: 'Claude Code tools' });
  expect(within(tools).getAllByRole('switch').map(item => item.getAttribute('aria-checked'))).toEqual(['true', 'true', 'true', 'true']);
  await user.click(within(tools).getByRole('switch', { name: 'Bash' }));
  await user.click(screen.getByRole('button', { name: 'Save details' }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledWith(expect.objectContaining({ runtime: 'claude_code', claude_code_tools: ['Read', 'Grep', 'Glob'] })));
  await user.click(await screen.findByRole('combobox', { name: 'Model connection' }));
  expect(screen.getByRole('option', { name: 'Claude on Bedrock' })).toBeVisible();
  expect(screen.queryByRole('option', { name: 'GPT' })).toBeNull();
});

test('a native agent shows its type, every compatible model and no Claude Code tools', async () => {
  const user = userEvent.setup();
  open('/agents/search');
  expect(await screen.findByRole('combobox', { name: 'Agent type' })).toHaveTextContent('Native');
  expect(screen.queryByRole('group', { name: 'Claude Code tools' })).toBeNull();
  await user.click(await screen.findByRole('combobox', { name: 'Model connection' }));
  expect(screen.getByRole('option', { name: 'GPT' })).toBeVisible();
  expect(screen.getByRole('option', { name: 'Claude on Bedrock' })).toBeVisible();
});

test('tool tabs say what Claude Code gets now, and Test starts a session', async () => {
  const user = userEvent.setup();
  open('/agents/writer?view=documents');
  expect(await screen.findByText(/get these tools in the next update/)).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Code projects' }));
  expect(screen.getByText(/read-only working folders/)).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Skills' }));
  expect(screen.queryByText(/next update/)).toBeNull();
  await user.click(screen.getByRole('tab', { name: 'Versions' }));
  expect(screen.getByText('Versions without Test vN')).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  expect(screen.queryByText('Native test chat')).toBeNull();
  expect(screen.getByText('Test in a Claude Code session')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Start a session' })).toHaveAttribute('href', '/documentation/generations/new?scope=agent&agent=writer');
});

test('a native agent keeps its test chat and Test vN', async () => {
  open('/agents/search?view=test');
  expect(await screen.findByText('Native test chat')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Start a session' })).toBeNull();
});

test('All agents shows the agent type and New agent opens one wizard', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgents).mockResolvedValue({ agents: [
    { ...writer, id: 'search', name: 'Search agent', runtime: 'openai', documents_enabled: true, model_name: 'GPT', prompt_title: 'Search', prompt_version: 2 },
    { ...writer, model_name: 'Claude on Bedrock', prompt_title: 'Built-in documentation writer', prompt_version: 1 }] });
  render(<MemoryRouter initialEntries={['/agents']}><Routes><Route path="/agents" element={<AgentsView />} /></Routes></MemoryRouter>);
  const row = (await screen.findByRole('link', { name: 'Technical writer' })).closest('tr')!;
  expect(screen.getByRole('columnheader', { name: 'Agent type' })).toBeVisible();
  expect(within(row).getByText('Claude Code')).toBeVisible();
  expect(within(row).getByText('Claude on Bedrock')).toBeVisible();
  expect(within(row).getByText('v1 · Built-in documentation writer')).toBeVisible();
  const other = screen.getByRole('link', { name: 'Search agent' }).closest('tr')!;
  expect(within(other).getByText('Native')).toBeVisible();
  expect(within(other).getByText('Documents')).toBeVisible();
  await user.type(screen.getByLabelText('Find agents'), 'claude code');
  expect(screen.queryByRole('link', { name: 'Search agent' })).toBeNull();
  await user.click(screen.getByRole('button', { name: 'New agent' }));
  expect(screen.getByText('Agent wizard')).toBeVisible();
  expect(screen.queryByRole('tab')).toBeNull();
});
