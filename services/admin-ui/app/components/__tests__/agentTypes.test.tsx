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
vi.mock('../AgentWizard', () => ({ default: () => <div>Agent wizard</div> }));

const writer: profiles.AgentProfile = { id: 'writer', name: 'Technical writer', description: '', when_to_use: '', enabled: true,
  runtime: 'claude_code', claude_code_tools: ['Read', 'Grep', 'Glob', 'Bash'], documents_enabled: false,
  repositories_enabled: false, ssh_enabled: false, skills_enabled: false, mcp_enabled: false, services_enabled: false,
  documents_grounded: false, revision: 1, model_id: 'claude', all_collections: false, all_repositories: false, collection_ids: [],
  repository_ids: [], service_ids: [], skill_ids: [], system_prompt: 'You are an expert technical writer.' };
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
const TABS = ['General', 'Tools', 'Skills', 'Documents', 'Code projects', 'SSH', 'MCP', 'Services', 'Test'];

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
  await user.click(await screen.findByRole('combobox', { name: 'Model' }));
  expect(screen.getByRole('option', { name: 'Claude on Bedrock' })).toBeVisible();
  expect(screen.queryByRole('option', { name: 'GPT' })).toBeNull();
});

test('a native agent shows its type, every compatible model and no Claude Code tools', async () => {
  const user = userEvent.setup();
  open('/agents/search');
  expect(await screen.findByRole('combobox', { name: 'Agent type' })).toHaveTextContent('Native');
  expect(screen.queryByRole('group', { name: 'Claude Code tools' })).toBeNull();
  await user.click(await screen.findByRole('combobox', { name: 'Model' }));
  expect(screen.getByRole('option', { name: 'GPT' })).toBeVisible();
  expect(screen.getByRole('option', { name: 'Claude on Bedrock' })).toBeVisible();
  expect(screen.queryByText('Save model')).toBeNull();
});

test('General saves the model with the details, and a type switch clears a model that no longer fits', async () => {
  const user = userEvent.setup();
  open('/agents/search');
  await user.click(await screen.findByRole('combobox', { name: 'Model' }));
  await user.click(screen.getByRole('option', { name: 'Claude on Bedrock' }));
  await user.click(screen.getByRole('button', { name: 'Save details' }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenLastCalledWith(expect.objectContaining({ runtime: 'openai', model_id: 'claude' })));
  expect(profiles.saveProfileModel).not.toHaveBeenCalled();
  await user.click(screen.getByRole('combobox', { name: 'Model' }));
  await user.click(screen.getByRole('option', { name: 'GPT' }));
  await user.click(screen.getByRole('combobox', { name: 'Agent type' }));
  await user.click(screen.getByRole('option', { name: 'Claude Code' }));
  expect(screen.getByRole('combobox', { name: 'Model' })).toHaveTextContent('Not configured');
  await user.click(screen.getByRole('button', { name: 'Save details' }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenLastCalledWith(expect.objectContaining({ runtime: 'claude_code', model_id: null })));
});

test('tool tabs say what Claude Code gets now, and Test no longer starts sessions', async () => {
  const user = userEvent.setup();
  open('/agents/writer?view=documents');
  expect(await screen.findByText(/get these tools in the next update/)).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Code projects' }));
  expect(screen.getByText(/read-only working folders/)).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Skills' }));
  expect(screen.queryByText(/next update/)).toBeNull();
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  expect(screen.queryByText('Native test chat')).toBeNull();
  expect(screen.getByText('Claude Code agents can no longer start sessions. Use a native agent.')).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Start a session' })).toBeNull();
});

test('a native agent keeps its test chat', async () => {
  open('/agents/search?view=test');
  expect(await screen.findByText('Native test chat')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Start a session' })).toBeNull();
});

test('All agents shows the agent type and New agent opens one wizard', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgents).mockResolvedValue({ agents: [
    { ...writer, id: 'search', name: 'Search agent', runtime: 'openai', documents_enabled: true, model_name: 'GPT' },
    { ...writer, model_name: 'Claude on Bedrock' }] });
  render(<MemoryRouter initialEntries={['/agents']}><Routes><Route path="/agents" element={<AgentsView />} /></Routes></MemoryRouter>);
  const row = (await screen.findByRole('link', { name: 'Technical writer' })).closest('tr')!;
  expect(screen.getByRole('columnheader', { name: 'Agent type' })).toBeVisible();
  expect(within(row).getByText('Claude Code')).toBeVisible();
  expect(within(row).getByText('Claude on Bedrock')).toBeVisible();
  const other = screen.getByRole('link', { name: 'Search agent' }).closest('tr')!;
  expect(within(other).getByText('Native')).toBeVisible();
  expect(within(other).getByText('Documents')).toBeVisible();
  await user.type(screen.getByLabelText('Find agents'), 'claude code');
  expect(screen.queryByRole('link', { name: 'Search agent' })).toBeNull();
  await user.click(screen.getByRole('button', { name: 'New agent' }));
  expect(screen.getByText('Agent wizard')).toBeVisible();
  expect(screen.queryByRole('tab')).toBeNull();
});

test('General sets Resources next to the agent type', async () => {
  const user = userEvent.setup();
  open('/agents/search');
  const resources = await screen.findByRole('combobox', { name: 'Resources' });
  expect(resources).toHaveTextContent('Fixed');
  expect(screen.getByText('Always works with the resources selected in its tabs.')).toBeVisible();
  await user.click(resources);
  await user.click(screen.getByRole('option', { name: 'Per run' }));
  expect(screen.getByText(/^Each run says what to work on/)).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Save details' }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledWith(expect.objectContaining({ id: 'search', resources: 'per_run' })));
});

test('All agents shows how each agent gets its resources', async () => {
  vi.mocked(profiles.getAgents).mockResolvedValue({ agents: [
    { ...writer, id: 'search', name: 'Search agent', runtime: 'openai', resources: 'fixed', model_name: 'GPT' },
    { ...writer, resources: 'per_run', model_name: 'Claude on Bedrock' }] });
  render(<MemoryRouter initialEntries={['/agents']}><Routes><Route path="/agents" element={<AgentsView />} /></Routes></MemoryRouter>);
  const row = (await screen.findByRole('link', { name: 'Technical writer' })).closest('tr')!;
  expect(screen.getByRole('columnheader', { name: 'Resources' })).toBeVisible();
  expect(within(row).getByText('Per run · nothing')).toBeVisible();
  expect(within(screen.getByRole('link', { name: 'Search agent' }).closest('tr')!).getByText('Fixed')).toBeVisible();
});

test('the list says what a per-run agent accepts', async () => {
  vi.mocked(profiles.getAgents).mockResolvedValue({ agents: [
    { ...writer, resources: 'per_run', accepts: [{ kind: 'code', limit: 'any' }, { kind: 'servers', limit: 2 }],
      model_name: 'Claude on Bedrock' }] });
  render(<MemoryRouter initialEntries={['/agents']}><Routes><Route path="/agents" element={<AgentsView />} /></Routes></MemoryRouter>);
  const row = (await screen.findByRole('link', { name: 'Technical writer' })).closest('tr')!;
  expect(within(row).getByText('Per run · code (any) · servers (2)')).toBeVisible();
});

test('capability tabs of a per-run agent say what callers may pass', async () => {
  vi.mocked(profiles.getAgent).mockImplementation(async id => ({ ...structuredClone(writer), id, runtime: 'openai', model_id: 'gpt', resources: 'per_run' }));
  const user = userEvent.setup();
  open('/agents/search?view=git');
  expect(await screen.findByText('Per run: callers may pass any of the code projects selected here. All code projects = any code project.')).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'SSH' }));
  expect(screen.getByText(/^Per run: callers may pass any of the servers selected here/)).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Skills' }));
  expect(screen.queryByText(/^Per run:/)).toBeNull();
});

test('All agents shows each agent\'s tools', async () => {
  vi.mocked(profiles.getAgents).mockResolvedValue({ agents: [
    { ...structuredClone(writer), id: 'analyst', name: 'Analyst', runtime: 'openai', model_name: null,
      tools_summary: { on: 3, per_run: 0, groups: { code: 1, documents: 0, servers: 1, mcp: 1, agents: 0 } } },
    { ...structuredClone(writer), model_name: null,
      tools_summary: { claude_code_tools: ['Read', 'Grep', 'Glob', 'Bash'] } }] });
  render(<MemoryRouter><AgentsView /></MemoryRouter>);
  expect(await screen.findByRole('columnheader', { name: 'Tools' })).toBeVisible();
  expect(within(screen.getByText('Analyst').closest('tr')!).getByText('3 tools · Code 1 · Servers 1 · MCP 1')).toBeVisible();
  expect(within(screen.getByText('Technical writer').closest('tr')!).getByText('Read, Grep, Glob, Bash')).toBeVisible();
});

test('General switches May start subagents, for native agents only', async () => {
  const user = userEvent.setup();
  const view = open('/agents/search');
  const subagents = await screen.findByRole('switch', { name: 'May start subagents' });
  expect(subagents).not.toBeChecked();
  expect(screen.getByText('The agent can hand separate parts of its work to helpers that run in parallel.')).toBeVisible();
  await user.click(subagents);
  await user.click(screen.getByRole('button', { name: 'Save details' }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledWith(expect.objectContaining({ id: 'search', subagents: true })));
  view.unmount();
  open('/agents/writer');
  await screen.findByRole('combobox', { name: 'Agent type' });
  expect(screen.queryByRole('switch', { name: 'May start subagents' })).toBeNull();
});
