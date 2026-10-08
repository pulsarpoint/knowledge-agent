import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import AgentDetailView from '../AgentDetailView';
import * as profiles from '../../lib/profiles';
import * as tools from '../../lib/tools';
import * as llm from '../../lib/llm';
import { Toaster } from '~/components/ui/sonner';
import { ApiError } from '../../lib/api';

vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgent: vi.fn(), getAgentOptions: vi.fn(), saveAgent: vi.fn() }));
vi.mock('../../lib/tools', async () => ({ ...await vi.importActual('../../lib/tools'), getAgentTools: vi.fn(), saveAgentTools: vi.fn() }));
vi.mock('../../lib/llm', async () => ({ ...await vi.importActual('../../lib/llm'), getLlmSettings: vi.fn() }));
vi.mock('../OperationsView', () => ({ default: () => null }));
vi.mock('../PromptsView', () => ({ default: () => null }));
vi.mock('../AgentInstructions', () => ({ default: () => null }));
vi.mock('../AgentVersionsView', () => ({ default: () => null }));
vi.mock('../AgentModelSelection', () => ({ default: () => null }));

const agent: profiles.AgentProfile = { id: 'payments', name: 'Payments analyst', description: '', when_to_use: '', enabled: true,
  runtime: 'openai', claude_code_tools: ['Read', 'Grep', 'Glob', 'Bash'], documents_enabled: false, repositories_enabled: true,
  ssh_enabled: false, skills_enabled: false, mcp_enabled: true, services_enabled: false, documents_grounded: false, revision: 4,
  model_id: null, all_collections: false, all_repositories: true, collection_ids: [], repository_ids: [], service_ids: [],
  skill_ids: [], active_prompt: null };
const tool = (patch: Partial<tools.AgentTool>): tools.AgentTool => ({ tool: 'read_file', label: 'Read file',
  summary: 'Read a file or a line range.', group: 'code', mode: 'on', usage: null, default_usage: 'Read a source line range.',
  subagent_allowed: true, available: true, reason: null, tab: 'git', ...patch });
const native: tools.AgentTools = { runtime: 'openai', revision: 4, tools: [
  tool({}),
  tool({ tool: 'shell_run', label: 'Shell', summary: 'Run Bash in a sandbox.', mode: 'off', default_usage: 'Run Bash commands.' }),
  tool({ tool: 'ssh_run', label: 'SSH', summary: 'Run a command on a selected server.', group: 'servers', available: false,
    reason: 'Needs SSH servers', tab: 'ssh', default_usage: 'Execute a command.' }),
  tool({ tool: 'mcp:m1:lookup', label: 'Docs / lookup', summary: '', group: 'mcp', usage: 'Use for API pages.',
    default_usage: 'Look up docs.', tab: 'mcp' }),
] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ is_admin: true })));
  vi.mocked(profiles.getAgent).mockImplementation(async id => ({ ...structuredClone(agent), id, runtime: id === 'writer' ? 'claude_code' : 'openai' }));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
  vi.mocked(llm.getLlmSettings).mockResolvedValue({ models: [], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
  vi.mocked(tools.getAgentTools).mockImplementation(async id => id === 'writer'
    ? { runtime: 'claude_code', revision: 1, claude_code_tools: ['Read', 'Grep'], tools: [] } : structuredClone(native));
  vi.mocked(tools.saveAgentTools).mockImplementation(async (_id, revision, saved) => ({ ...native, revision: revision + 1, tools: saved }));
});
afterEach(() => vi.unstubAllGlobals());
function Detail() { const { id } = useParams(); return <AgentDetailView key={id} agentId={id!} />; }
const open = (path: string) => render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/agents/:id" element={<Detail />} /></Routes><Toaster /></MemoryRouter>);
const row = (name: string) => screen.getByRole('switch', { name }).closest('[data-tool]') as HTMLElement;

test('the Tools tab switches tools, edits and resets usage and saves once', async () => {
  const user = userEvent.setup();
  open('/agents/payments?view=tools');
  expect(await screen.findByRole('group', { name: 'Code' })).toBeVisible();
  expect(screen.getAllByRole('tab').map(tab => tab.textContent).slice(0, 2)).toEqual(['General', 'Tools']);
  expect(screen.queryByRole('group', { name: 'Agents' })).toBeNull();  // empty groups are hidden
  await user.click(screen.getByRole('switch', { name: 'Shell' }));
  await user.click(within(row('Read file')).getByRole('button', { name: 'Edit usage' }));
  const usage = screen.getByLabelText('Usage for Read file');
  expect(usage).toHaveValue('Read a source line range.');
  await user.clear(usage); await user.type(usage, 'Read only named files.');
  expect(within(row('Read file')).getByText('Edited')).toBeVisible();
  expect(within(row('Docs / lookup')).getByText('Edited')).toBeVisible();
  await user.click(within(row('Docs / lookup')).getByRole('button', { name: 'Edit usage' }));
  await user.click(within(row('Docs / lookup')).getByRole('button', { name: 'Reset' }));
  expect(within(row('Docs / lookup')).queryByText('Edited')).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  await waitFor(() => expect(tools.saveAgentTools).toHaveBeenCalledTimes(1));
  const [id, revision, saved] = vi.mocked(tools.saveAgentTools).mock.calls[0];
  expect([id, revision]).toEqual(['payments', 4]);
  expect(saved.map(({ tool: name, mode, usage: text }) => [name, mode, text])).toEqual([
    ['read_file', 'on', 'Read only named files.'], ['shell_run', 'on', null], ['ssh_run', 'on', null], ['mcp:m1:lookup', 'on', null]]);
  expect(await screen.findByText('Tools saved.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save tools' })).toBeDisabled();
});

test('unmet needs are greyed out with their reason and link', async () => {
  open('/agents/payments?view=tools');
  const ssh = await screen.findByRole('switch', { name: 'SSH' });
  const line = ssh.closest('[data-tool]') as HTMLElement;
  expect(line).toHaveClass('opacity-60');
  expect(within(line).getByText(/Needs SSH servers/)).toBeVisible();
  expect(within(line).getByRole('link', { name: 'Open SSH' })).toHaveAttribute('href', '/agents/payments?view=ssh');
  expect(row('Read file')).not.toHaveClass('opacity-60');
});

test('capability tabs of a native agent point to the Tools tab', async () => {
  open('/agents/payments?view=git');
  expect(await screen.findByText(tools.TOOLS_TAB_NOTE)).toBeVisible();
});

test('a Claude Code agent lists its Claude Code tools', async () => {
  open('/agents/writer?view=tools');
  const list = await screen.findByRole('list', { name: 'Claude Code tools' });
  expect(within(list).getAllByRole('listitem').map(item => item.textContent)).toEqual(['Read', 'Grep']);
  expect(screen.getByText(tools.CLAUDE_CODE_TOOLS_NOTE)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Save tools' })).toBeNull();
  expect(screen.queryByRole('switch', { name: 'Read file' })).toBeNull();
});

test('a stale save offers a reload that brings back the saved tools', async () => {
  const user = userEvent.setup();
  vi.mocked(tools.saveAgentTools).mockRejectedValueOnce(new ApiError('The tools changed since you opened them. Reload.', 409));
  open('/agents/payments?view=tools');
  await user.click(await screen.findByRole('switch', { name: 'Shell' }));
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  expect(await screen.findByText('The tools changed since you opened them. Reload.')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Reload tools' }));
  await waitFor(() => expect(tools.getAgentTools).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole('switch', { name: 'Shell' })).not.toBeChecked();
  expect(screen.queryByText('The tools changed since you opened them. Reload.')).toBeNull();
});

test('saving tools hands the new revision to the General tab and a General save reloads the tools', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.saveAgent).mockImplementation(async value => ({ ...structuredClone(agent), ...value, revision: value.revision + 1 }));
  open('/agents/payments?view=tools');
  await user.click(await screen.findByRole('switch', { name: 'Shell' }));
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  await screen.findByText('Tools saved.');
  await user.click(screen.getByRole('tab', { name: 'General' }));
  const name = screen.getByRole('textbox', { name: 'Name' });
  await user.type(name, ' 2');
  await user.click(screen.getByRole('button', { name: 'Save details' }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledTimes(1));
  expect(vi.mocked(profiles.saveAgent).mock.calls[0][0]).toMatchObject({ revision: 5 });
  await waitFor(() => expect(tools.getAgentTools).toHaveBeenCalledTimes(2));
});
