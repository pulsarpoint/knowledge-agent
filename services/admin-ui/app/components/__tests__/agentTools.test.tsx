import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import AgentDetailView from '~/features/agents/AgentDetailView';
import { keepChoices } from '~/features/agents/AgentToolsView';
import * as profiles from '~/features/agents/api';
import * as tools from '~/features/agents/tools';
import * as llm from "~/features/llm/api";
import { Toaster } from '~/components/ui/sonner';
import { ApiError } from '../../lib/api';

vi.mock('~/features/agents/api', async () => ({ ...await vi.importActual('~/features/agents/api'), getAgent: vi.fn(), getAgentOptions: vi.fn(), saveAgent: vi.fn() }));
vi.mock('~/features/agents/tools', async () => ({ ...await vi.importActual('~/features/agents/tools'), getAgentTools: vi.fn(), saveAgentTools: vi.fn() }));
vi.mock("~/features/llm/api", async () => ({ ...await vi.importActual("~/features/llm/api"), getLlmSettings: vi.fn() }));
vi.mock('~/features/servers/OperationsView', () => ({ default: () => null }));

const agent: profiles.AgentProfile = { id: 'payments', name: 'Payments analyst', description: '', when_to_use: '', enabled: true,
  documents_enabled: false, repositories_enabled: true,
  ssh_enabled: false, skills_enabled: false, mcp_enabled: true, services_enabled: false, documents_grounded: false, revision: 4,
  model_id: null, all_collections: false, all_repositories: true, collection_ids: [], repository_ids: [], service_ids: [],
  skill_ids: [], system_prompt: '' };
const tool = (patch: Partial<tools.AgentTool>): tools.AgentTool => ({ tool: 'read_file', label: 'Read file',
  summary: 'Read a file or a line range.', group: 'code', mode: 'on', usage: null, default_usage: 'Read a source line range.',
  subagent_allowed: true, available: true, reason: null, tab: 'git', ...patch });
const native: tools.AgentTools = { revision: 4, tools: [
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
  vi.mocked(profiles.getAgent).mockImplementation(async id => ({ ...structuredClone(agent), id }));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
  vi.mocked(llm.getLlmSettings).mockResolvedValue({ models: [], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
  vi.mocked(tools.getAgentTools).mockImplementation(async () => structuredClone(native));
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
  expect(screen.queryByRole('group', { name: 'Documents' })).toBeNull();  // empty groups are hidden
  expect(within(screen.getByRole('group', { name: 'Agents' })).getByText(tools.SUBAGENTS_OFF_NOTE)).toBeVisible();
  await user.click(screen.getByRole('switch', { name: 'Shell' }));
  const toggle = within(row('Read file')).getByRole('button', { name: 'Edit usage for Read file' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await user.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  expect(toggle).toHaveAccessibleName('Close usage for Read file');
  expect(document.getElementById(toggle.getAttribute('aria-controls')!)).toContainElement(screen.getByLabelText('Usage for Read file'));
  const usage = screen.getByLabelText('Usage for Read file');
  expect(usage).toHaveValue('Read a source line range.');
  await user.clear(usage); await user.type(usage, 'Read only named files.');
  expect(within(row('Read file')).getByText('Edited')).toBeVisible();
  expect(within(row('Docs / lookup')).getByText('Edited')).toBeVisible();
  await user.click(within(row('Docs / lookup')).getByRole('button', { name: 'Edit usage for Docs / lookup' }));
  expect(screen.getByLabelText('Usage for Docs / lookup')).toHaveValue('Use for API pages.');
  await user.click(within(row('Docs / lookup')).getByRole('button', { name: 'Reset usage for Docs / lookup' }));
  expect(screen.getByLabelText('Usage for Docs / lookup')).toHaveValue('Look up docs.');
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
  expect(within(line).getByText(/Needs SSH servers/)).toBeVisible();
  expect(within(line).getByRole('link', { name: 'Open SSH' })).toHaveAttribute('href', '/agents/payments?view=ssh');
  expect(within(row('Read file')).queryByText(/Needs/)).toBeNull();
});

test('capability tabs of a native agent point to the Tools tab', async () => {
  open('/agents/payments?view=git');
  expect(await screen.findByText(tools.TOOLS_TAB_NOTE)).toBeVisible();
});

test('an emptied usage saves as the default text', async () => {
  const user = userEvent.setup();
  open('/agents/payments?view=tools');
  await user.click(await screen.findByRole('button', { name: 'Edit usage for Docs / lookup' }));
  expect(screen.getByText(/Empty restores the default text/)).toBeVisible();
  await user.clear(screen.getByLabelText('Usage for Docs / lookup'));
  expect(screen.getByLabelText('Usage for Docs / lookup')).toHaveValue('');
  expect(within(row('Docs / lookup')).queryByText('Edited')).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  await waitFor(() => expect(tools.saveAgentTools).toHaveBeenCalledTimes(1));
  expect(vi.mocked(tools.saveAgentTools).mock.calls[0][2].find(item => item.tool === 'mcp:m1:lookup')?.usage).toBeNull();
});

/** A server that answers with its current revision and refuses saves made against an older one. */
function server(start: { revision: number; ssh: boolean }) {
  const state = { ...start };
  vi.mocked(tools.getAgentTools).mockImplementation(async () => ({ ...structuredClone(native), revision: state.revision,
    tools: native.tools.map(item => item.tool === 'ssh_run' && state.ssh ? { ...item, available: true, reason: null } : item) }));
  vi.mocked(tools.saveAgentTools).mockImplementation(async (_id, revision, saved) => {
    if (revision !== state.revision) throw new ApiError('The tools changed since you opened them. Reload.', 409);
    state.revision += 1;
    return { ...native, revision: state.revision, tools: saved };
  });
  vi.mocked(profiles.saveAgent).mockImplementation(async value => {
    state.revision += 1; state.ssh = value.ssh_enabled;
    return { ...structuredClone(agent), ...value, revision: state.revision };
  });
  return state;
}

test('a capability saved on another tab keeps unsaved tool choices and refreshes availability', async () => {
  const user = userEvent.setup();
  server({ revision: 4, ssh: false });
  open('/agents/payments?view=tools');
  await user.click(await screen.findByRole('switch', { name: 'Shell' }));
  expect(within(row('SSH')).getByText(/Needs SSH servers/)).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'SSH' }));
  await user.click(screen.getByRole('switch', { name: 'Enable SSH access' }));
  await user.click(screen.getByRole('button', { name: 'Save SSH access' }));
  await waitFor(() => expect(profiles.saveAgent).toHaveBeenCalledTimes(1));
  await user.click(screen.getByRole('tab', { name: 'Tools' }));
  await waitFor(() => expect(within(row('SSH')).queryByText(/Needs SSH servers/)).toBeNull());
  expect(screen.getByRole('switch', { name: 'Shell' })).toBeChecked();
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  expect(await screen.findByText('Tools saved.')).toBeInTheDocument();  // a toast; sonner animates it in
  expect(screen.getByRole('button', { name: 'Save tools' })).toBeDisabled();
  expect(vi.mocked(tools.saveAgentTools).mock.calls.at(-1)?.[1]).toBe(5);
  expect(screen.getByRole('switch', { name: 'Shell' })).toBeChecked();
  expect(screen.queryByText(/The tools changed/)).toBeNull();
});

test('a stale save keeps the draft so a retry saves it, and Reload tools discards it on request', async () => {
  const user = userEvent.setup();
  const state = server({ revision: 4, ssh: false });
  open('/agents/payments?view=tools');
  await user.click(await screen.findByRole('switch', { name: 'Shell' }));
  state.revision = 6;  // someone else saved in between
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  expect(await screen.findByText(/The tools changed since you opened them/)).toBeVisible();
  expect(screen.getByRole('switch', { name: 'Shell' })).toBeChecked();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save tools' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  expect(await screen.findByText('Tools saved.')).toBeInTheDocument();  // a toast; sonner animates it in
  expect(screen.getByRole('button', { name: 'Save tools' })).toBeDisabled();
  expect(vi.mocked(tools.saveAgentTools).mock.calls.at(-1)?.[1]).toBe(6);
  expect(screen.queryByText(/The tools changed/)).toBeNull();

  await user.click(screen.getByRole('switch', { name: 'Read file' }));
  state.revision = 9;
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  await user.click(await screen.findByRole('button', { name: 'Reload tools' }));
  await waitFor(() => expect(screen.getByRole('switch', { name: 'Read file' })).toBeChecked());
  expect(screen.queryByText(/The tools changed/)).toBeNull();
  expect(screen.getByRole('button', { name: 'Save tools' })).toBeDisabled();
});

test('with subagents on, the Agents group edits run_subagent and what a subagent may use', async () => {
  const user = userEvent.setup();
  vi.mocked(tools.getAgentTools).mockResolvedValue({ ...structuredClone(native), tools: [...structuredClone(native.tools),
    tool({ tool: 'run_subagent', label: 'Start subagents', summary: 'Hand a separate part of the work to a helper that runs in parallel.',
      group: 'agents', subagent_allowed: false, default_usage: 'Start a helper for a separate, well-defined part of your work.', tab: null })] });
  open('/agents/payments?view=tools');
  const agents = await screen.findByRole('group', { name: 'Agents' });
  const start = within(agents).getByRole('switch', { name: 'Start subagents' });
  expect(start).toBeChecked();
  expect(start).toBeDisabled();  // switched with May start subagents on General
  const may = within(agents).getByRole('group', { name: 'A subagent may use' });
  expect(within(may).getByRole('checkbox', { name: 'Read file' })).toBeChecked();
  expect(within(may).getByRole('checkbox', { name: 'Docs / lookup' })).toBeChecked();
  expect(within(may).queryByRole('checkbox', { name: 'Shell' })).toBeNull();  // off: not one of the agent's tools
  expect(within(may).queryByRole('checkbox', { name: 'Start subagents' })).toBeNull();
  await user.click(within(may).getByRole('checkbox', { name: 'Read file' }));
  await user.click(within(agents).getByRole('button', { name: 'Edit usage for Start subagents' }));
  const usage = screen.getByLabelText('Usage for Start subagents');
  await user.clear(usage); await user.type(usage, 'Split the work by project.');
  expect(within(agents).getByText('Edited')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  await waitFor(() => expect(tools.saveAgentTools).toHaveBeenCalledTimes(1));
  const saved = vi.mocked(tools.saveAgentTools).mock.calls[0][2];
  expect(saved.find(item => item.tool === 'read_file')?.subagent_allowed).toBe(false);
  expect(saved.find(item => item.tool === 'run_subagent')).toMatchObject({ mode: 'on', usage: 'Split the work by project.' });
  await user.click(within(agents).getByRole('button', { name: 'Edit usage for Start subagents' }));
  await user.click(within(agents).getByRole('button', { name: 'Reset usage for Start subagents' }));
  expect(within(agents).queryByText('Edited')).toBeNull();
  expect(screen.getByLabelText('Usage for Start subagents')).toHaveValue('Start a helper for a separate, well-defined part of your work.');
});

test('fresh rows keep only the choices the user touched', () => {
  const fresh = [tool({}), tool({ tool: 'shell_run', mode: 'off' }), tool({ tool: 'ssh_run', group: 'servers', mode: 'on' })];
  const draft = [tool({ usage: 'Narrow reads.', subagent_allowed: false }), tool({ tool: 'shell_run', mode: 'on' }), tool({ tool: 'ssh_run', group: 'servers', mode: 'off' })];
  // ssh_run was switched on by a General save; its stale 'off' in the draft was never touched.
  expect(keepChoices(fresh, draft, new Set(['read_file', 'shell_run'])).map(item => [item.tool, item.mode, item.usage, item.subagent_allowed])).toEqual([
    ['read_file', 'on', 'Narrow reads.', false], ['shell_run', 'on', null, true], ['ssh_run', 'on', null, true]]);
});
