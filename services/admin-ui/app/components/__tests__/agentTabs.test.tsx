import { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import AgentsView from '../AgentsView';
import AgentDetailView from '../AgentDetailView';
import AdminLayout from '../AdminLayout';
import PromptsPage from '../../routes/prompts';
import * as prompts from '../../lib/prompts';
import * as llm from '../../lib/llm';
import * as profiles from '../../lib/profiles';

vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgents: vi.fn(), getAgent: vi.fn(), getAgentOptions: vi.fn(), saveAgent: vi.fn(), deleteAgent: vi.fn(), getAgentHistory: vi.fn() }));
vi.mock('../../lib/mcp', () => ({ getMCPServers: vi.fn(async () => ({ servers: [] })), getCodeMCP: vi.fn(async () => ({ revision: 1, mcp_server_ids: [] })), saveCodeMCP: vi.fn() }));
vi.mock('../CodeSearchAgentView', () => ({ default: ({ agentId }: { agentId: string }) => <div>Code test {agentId}</div> }));
vi.mock('../OperationsView', () => ({ default: ({ view, agentId }: { view: string; agentId: string }) => {
  const [task, setTask] = useState('');
  return <div hidden={view !== 'test'}><span>SSH test {agentId}</span><label>SSH task<input value={task} onChange={event => setTask(event.target.value)} /></label></div>;
} }));
vi.mock('../../lib/prompts', async () => ({ ...await vi.importActual('../../lib/prompts'), getPrompts: vi.fn(), savePrompt: vi.fn(), activatePrompt: vi.fn() }));
vi.mock('../../lib/llm', async () => ({ ...await vi.importActual('../../lib/llm'), getLlmSettings: vi.fn() }));
let data: prompts.Prompts;
let agents: profiles.AgentSummary[];
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))));
  vi.mocked(llm.getLlmSettings).mockResolvedValue({ models: [], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
  agents = ['search', 'operations', 'code', 'custom-code'].map(id => ({ id, documents_grounded: false, name: id === 'custom-code' ? 'Payments code' : prompts.agentNames[id as prompts.AgentName], documents_enabled: id === 'search', repositories_enabled: id !== 'operations', ssh_enabled: id === 'operations', skills_enabled: true, mcp_enabled: id !== 'search', services_enabled: true, description: '', when_to_use: '', enabled: true, revision: 1, model_id: null, all_collections: false, all_repositories: false, collection_ids: [], repository_ids: [], service_ids: [], skill_ids: [], active_prompt: null, model_name: null, prompt_title: `${id} prompt`, prompt_version: 3 }));
  data = {
    agents: agents.map(a => ({ agent: a.id, active_version_id: a.id, revision: 1 })),
    versions: agents.map(a => ({ agent: a.id, id: a.id, version: 3, title: `${a.id} prompt`, content: `${a.id} instructions`, revision: 1, status: 'active', created_at: '', updated_at: '' })),
  };
  vi.mocked(prompts.getPrompts).mockImplementation(async () => structuredClone(data));
  vi.mocked(profiles.getAgents).mockImplementation(async () => ({ agents: structuredClone(agents) }));
  vi.mocked(profiles.getAgent).mockImplementation(async id => structuredClone(agents.find(a => a.id === id)!));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({ test_chats: 0, chats: 0, claude_code_sessions: 0, runs: 0 });
});
function Detail() { const { id } = useParams(); return <AgentDetailView key={id} agentId={id!} />; }
function app(path: string) { return <MemoryRouter initialEntries={[path]}><AdminLayout><Routes><Route path="/agents" element={<AgentsView />} /><Route path="/agents/:id" element={<Detail />} /><Route path="/llm-settings/prompts" element={<PromptsPage />} /></Routes></AdminLayout></MemoryRouter>; }

test('catalog opens distinct agents with one model card and preserves SSH tasks across sections', async () => {
  const user = userEvent.setup();
  render(app('/agents'));
  await user.click(await screen.findByRole('link', { name: 'SSH agent' }));
  await screen.findByRole('combobox', { name: 'Model connection' });
  expect(screen.getByRole('switch', { name: 'Enable agent' })).toBeEnabled();
  expect(screen.getAllByRole('button', { name: 'Save model', hidden: true })).toHaveLength(1);
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  await user.type(screen.getByRole('textbox', { name: 'SSH task' }), 'Inspect disk usage');
  await user.click(screen.getByRole('tab', { name: 'General' }));
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  expect(screen.getByRole('textbox', { name: 'SSH task' })).toHaveValue('Inspect disk usage');
  await user.click(screen.getByRole('tab', { name: 'General' }));
  expect(await screen.findByRole('link', { name: 'Active system prompt: operations prompt, version 3' })).toHaveAttribute('href', '/agents/operations?view=prompts#system-prompts-operations');
  await user.click(within(screen.getByRole('region', { name: 'Agent details' })).getByRole('link', { name: 'All agents' }));
  await user.click(await screen.findByRole('link', { name: 'Payments code' }));
  await screen.findByRole('combobox', { name: 'Model connection' });
  expect(screen.getAllByRole('combobox', { name: 'Model connection', hidden: true })).toHaveLength(1);
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  expect(screen.getByText('SSH test custom-code')).toBeVisible();
});

test('a custom agent prompt draft survives section changes and saves to its own identity', async () => {
  const user = userEvent.setup();
  vi.mocked(prompts.savePrompt).mockImplementation(async (agent, title, content) => {
    const version = { ...data.versions.find(row => row.agent === agent)!, id: 'draft', version: 4, title, content, status: 'draft' as const };
    data.versions.unshift(version); return version;
  });
  render(app('/agents/custom-code?view=prompts'));
  await user.click(await screen.findByRole('button', { name: 'New draft from this version' }));
  await user.type(screen.getByRole('textbox', { name: 'System prompt' }), ' Custom draft.');
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  await user.click(screen.getByRole('tab', { name: 'General' }));
  await user.click(screen.getByRole('tab', { name: 'Prompts' }));
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue('custom-code instructions Custom draft.');
  await user.click(screen.getByRole('button', { name: 'Save draft' }));
  await screen.findByText('Version 4 saved as draft. The active prompt has not changed.');
  expect(prompts.savePrompt).toHaveBeenCalledWith('custom-code', 'custom-code prompt', 'custom-code instructions Custom draft.', undefined);
  expect(prompts.activatePrompt).not.toHaveBeenCalled();
});

test('activating a prompt refreshes the Test preview', async () => {
  data.versions.unshift({ ...data.versions[0], id: 'next', version: 4, title: 'Revised search', content: 'New instructions.', status: 'draft' });
  vi.mocked(prompts.activatePrompt).mockImplementation(async version => {
    data.agents[0].active_version_id = version.id;
    data.versions = data.versions.map(row => row.agent !== 'search' ? row : { ...row, status: row.id === version.id ? 'active' : 'archived' });
    return data.versions.find(row => row.id === version.id)!;
  });
  const user = userEvent.setup();
  render(app('/agents/search?view=prompts'));
  await user.click(await screen.findByRole('button', { name: 'View version 4' }));
  await user.click(screen.getByRole('button', { name: 'Set active' }));
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Activate version' }));
  await screen.findByText('Version 4 is now active.');
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  expect(screen.queryByRole('link', { name: /Active system prompt/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole('tab', { name: 'General' }));
  expect(await screen.findByRole('link', { name: 'Active system prompt: Revised search, version 4' })).toBeVisible();
});

test('old prompt links open the matching agent detail', async () => {
  render(app('/llm-settings/prompts?agent=code'));
  expect(await screen.findByRole('heading', { name: 'Code search agent', level: 2 })).toBeVisible();
  expect(screen.getByRole('tab', { name: 'Prompts' })).toHaveAttribute('aria-selected', 'true');
  expect(await screen.findByRole('region', { name: 'Code search agent system prompts' })).toBeVisible();
});

test('agents are created only through one New agent panel with the wizard', async () => {
  render(app('/agents'));
  const button = await screen.findByRole('button', { name: 'New agent' });
  expect(screen.queryByRole('button', { name: 'New Agent Wizard' })).toBeNull();
  expect(screen.queryByLabelText('System prompt')).toBeNull();
  fireEvent.click(button);
  const panel = screen.getByRole('region', { name: 'New agent' });
  expect(within(panel).queryByRole('tab')).toBeNull();
  expect(within(panel).getByRole('button', { name: /Generate proposal/ })).toBeInTheDocument();
});

test.each(['search','operations','code','custom-code'])('every agent has the same capability tabs: %s', async id => {
  render(app(`/agents/${id}`));
  await screen.findByRole('combobox', { name: 'Model connection' });
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['General','Tools','Skills','Documents','Code projects','SSH','MCP','Services','Prompts','Versions','Test']);
});

test('Documents tab picks RAG collections by name without subfolder options', async () => {
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [{ id: 'c1', name: 'jira', description: 'Jira tickets' }], repositories: [], services: [], skills: [] });
  render(app('/agents/search?view=documents'));
  expect(await screen.findByRole('switch', { name: 'All collections' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'jira' })).toBeInTheDocument();
  expect(screen.queryByText(/Include subfolders/)).toBeNull();
});

test('Code SSH and SSH Git access can be enabled, then disabled without clearing resources', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.saveAgent).mockImplementation(async draft => ({ ...draft, revision: draft.revision + 1 }));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [{ id: 'repo', name: 'Payments repo' }], services: [], skills: [] });
  render(app('/agents/code?view=ssh'));
  expect(await screen.findByRole('switch', { name: 'Enable SSH access' })).not.toBeChecked();
  await user.click(screen.getByRole('switch', { name: 'Enable SSH access' }));
  await user.click(screen.getByRole('button', { name: 'Save SSH access' }));
  await screen.findByText('Agent configuration saved.');
  expect(profiles.saveAgent).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'code', ssh_enabled: true }));
  await user.click(within(screen.getByRole('region', { name: 'Agent details' })).getByRole('link', { name: 'All agents' }));
  await user.click(await screen.findByRole('link', { name: 'SSH agent' }));
  await user.click(await screen.findByRole('tab', { name: 'Code projects' }));
  expect(await screen.findByRole('switch', { name: 'Enable code project access' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Payments repo' })).toBeDisabled();
  await user.click(screen.getByRole('switch', { name: 'Enable code project access' }));
  await user.click(screen.getByRole('checkbox', { name: 'Payments repo' }));
  await user.click(screen.getByRole('button', { name: 'Save code project access' }));
  await screen.findByText('Agent configuration saved.');
  await user.click(screen.getByRole('switch', { name: 'Enable code project access' }));
  expect(screen.getByRole('checkbox', { name: 'Payments repo' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'Payments repo' })).toBeChecked();
  await user.click(screen.getByRole('button', { name: 'Save code project access' }));
  await screen.findByText('Agent configuration saved.');
  expect(profiles.saveAgent).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'operations', repositories_enabled: false, repository_ids: ['repo'] }));
});


test('Skills reveals multiple selections when enabled, saves together, and retains them when disabled', async () => {
  const user = userEvent.setup();
  agents.find(a => a.id === 'code')!.skills_enabled = false;
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [
    { id: 'inspect', name: 'Inspect code', description: 'Find implementation details', enabled: true },
    { id: 'verify', name: 'Verify findings', description: 'Check evidence', enabled: true },
  ] });
  vi.mocked(profiles.saveAgent).mockImplementation(async draft => ({ ...draft, revision: draft.revision + 1 }));
  render(app('/agents/code?view=skills'));
  const toggle = await screen.findByRole('switch', { name: 'Enable skills' });
  expect(toggle).not.toBeChecked();
  expect(screen.queryByRole('checkbox', { name: 'Inspect code' })).toBeNull();
  expect(screen.queryByRole('textbox', { name: 'Instructions' })).toBeNull();
  expect(screen.queryByRole('link', { name: 'Server SSH settings' })).toBeNull();
  expect(screen.getAllByRole('button', { name: 'Save skills' })).toHaveLength(1);
  await user.click(screen.getByRole('switch', { name: 'Enable skills' }));
  await user.click(screen.getByRole('checkbox', { name: 'Inspect code' }));
  await user.click(screen.getByRole('checkbox', { name: 'Verify findings' }));
  await user.click(screen.getByRole('tab', { name: 'General' }));
  await user.click(screen.getByRole('tab', { name: 'Skills' }));
  expect(screen.getByRole('checkbox', { name: 'Inspect code' })).toBeChecked();
  await user.click(screen.getByRole('button', { name: 'Save skills' }));
  await screen.findByText('Agent configuration saved.');
  expect(profiles.saveAgent).toHaveBeenLastCalledWith(expect.objectContaining({ skills_enabled: true }), ['inspect', 'verify']);
  await user.click(screen.getByRole('switch', { name: 'Enable skills' }));
  expect(screen.queryByRole('checkbox', { name: 'Inspect code' })).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Save skills' }));
  await screen.findByText('Agent configuration saved.');
  expect(profiles.saveAgent).toHaveBeenLastCalledWith(expect.objectContaining({ skills_enabled: false }), ['inspect', 'verify']);
  await user.click(screen.getByRole('switch', { name: 'Enable skills' }));
  expect(screen.getByRole('checkbox', { name: 'Inspect code' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Verify findings' })).toBeChecked();
});

test('agent removal requires confirmation and preserves the record on conflicts', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error('Agent has retained run history.')).mockResolvedValue(undefined);
  render(app('/agents'));
  await user.click(await screen.findByRole('button', { name: 'Remove Payments code' }));
  expect(screen.getByRole('alertdialog')).toHaveTextContent('Shared servers, code projects, skills, models, and documents will be kept.');
  expect(profiles.deleteAgent).not.toHaveBeenCalled();
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Cancel' }));
  expect(profiles.deleteAgent).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Remove Payments code' }));
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove agent' }));
  expect(await screen.findByText('Agent has retained run history.')).toBeVisible();
  expect(profiles.deleteAgent).toHaveBeenCalledWith('custom-code', 1, false);
  expect(screen.getByRole('alertdialog')).toBeVisible();
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove agent' }));
  expect(screen.queryByRole('button', { name: 'Remove Payments code' })).not.toBeInTheDocument();
  expect(await screen.findByText('Agent Payments code removed')).toBeVisible();
});

test('an agent with history lists it and is deleted with it only on the destructive action', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({ test_chats: 2, chats: 0, claude_code_sessions: 1, runs: 3 });
  vi.mocked(profiles.deleteAgent).mockResolvedValue(undefined);
  render(app('/agents'));
  await user.click(await screen.findByRole('button', { name: 'Remove Payments code' }));
  const dialog = screen.getByRole('alertdialog');
  expect(await within(dialog).findByText('2 test chats, 1 Claude Code session, 3 runs')).toBeVisible();
  expect(profiles.getAgentHistory).toHaveBeenCalledWith('custom-code');
  expect(dialog).toHaveTextContent('Saved documents are kept.');
  expect(within(dialog).queryByRole('button', { name: 'Remove agent' })).toBeNull();
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(profiles.deleteAgent).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Remove Payments code' }));
  await user.click(await within(screen.getByRole('alertdialog')).findByRole('button', { name: 'Delete agent and history' }));
  expect(profiles.deleteAgent).toHaveBeenCalledWith('custom-code', 1, true);
  expect(screen.queryByRole('button', { name: 'Remove Payments code' })).not.toBeInTheDocument();
  expect(await screen.findByText('Agent Payments code and its history deleted')).toBeVisible();
});

test('a delete refused because history appeared meanwhile shows that history', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory).mockResolvedValueOnce({ test_chats: 0, chats: 0, claude_code_sessions: 0, runs: 0 })
    .mockResolvedValue({ test_chats: 0, chats: 1, claude_code_sessions: 0, runs: 1 });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error('This agent has sessions or run history.'));
  render(app('/agents'));
  await user.click(await screen.findByRole('button', { name: 'Remove Payments code' }));
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove agent' }));
  expect(await within(screen.getByRole('alertdialog')).findByText('1 chat, 1 run')).toBeVisible();
  expect(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete agent and history' })).toBeEnabled();
});

const WORKING = 'This agent is working right now. Stop its documentation sessions and runs, or wait for them to finish, then delete it.';

test('a delete refused because the agent is working says so and keeps the dialog open', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({ test_chats: 0, chats: 0, claude_code_sessions: 1, runs: 0 });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error(WORKING));
  render(app('/agents'));
  await user.click(await screen.findByRole('button', { name: 'Remove Payments code' }));
  await user.click(await within(screen.getByRole('alertdialog')).findByRole('button', { name: 'Delete agent and history' }));
  expect(await within(screen.getByRole('alertdialog')).findByText(WORKING)).toBeVisible();
  expect(screen.getByRole('alertdialog')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Remove Payments code', hidden: true })).toBeInTheDocument();
});

test('a working agent whose history appeared meanwhile keeps the working message next to the history', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory).mockResolvedValueOnce({ test_chats: 0, chats: 0, claude_code_sessions: 0, runs: 0 })
    .mockResolvedValue({ test_chats: 0, chats: 0, claude_code_sessions: 1, runs: 0 });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error(WORKING));
  render(app('/agents'));
  await user.click(await screen.findByRole('button', { name: 'Remove Payments code' }));
  await user.click(await within(screen.getByRole('alertdialog')).findByRole('button', { name: 'Remove agent' }));
  const dialog = screen.getByRole('alertdialog');
  expect(await within(dialog).findByText('1 Claude Code session')).toBeVisible();
  expect(within(dialog).getByText(WORKING)).toBeVisible();
});
