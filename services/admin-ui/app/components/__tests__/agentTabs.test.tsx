import { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import AgentsView from '../AgentsView';
import AgentDetailView from '../AgentDetailView';
import AdminLayout from '../AdminLayout';
import * as llm from "~/features/llm/api";
import * as profiles from '../../lib/profiles';

vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgents: vi.fn(), getAgent: vi.fn(), getAgentOptions: vi.fn(), saveAgent: vi.fn(), deleteAgent: vi.fn(), getAgentHistory: vi.fn() }));
vi.mock("~/features/llm/mcp", () => ({ getMCPServers: vi.fn(async () => ({ servers: [] })) }));
vi.mock('../CodeSearchAgentView', () => ({ default: ({ agentId }: { agentId: string }) => <div>Code test {agentId}</div> }));
vi.mock('~/features/servers/OperationsView', () => ({ default: ({ view, agentId }: { view: string; agentId: string }) => {
  const [task, setTask] = useState('');
  return <div hidden={view !== 'test'}><span>SSH test {agentId}</span><label>SSH task<input value={task} onChange={event => setTask(event.target.value)} /></label></div>;
} }));
vi.mock("~/features/llm/api", async () => ({ ...await vi.importActual("~/features/llm/api"), getLlmSettings: vi.fn() }));
let agents: profiles.AgentSummary[];
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))));
  vi.mocked(llm.getLlmSettings).mockResolvedValue({ models: [], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
  agents = ['search', 'operations', 'code', 'custom-code'].map(id => ({ id, documents_grounded: false, name: id === 'custom-code' ? 'Payments code' : llm.agentNames[id as llm.AgentName], documents_enabled: id === 'search', repositories_enabled: id !== 'operations', ssh_enabled: id === 'operations', skills_enabled: true, mcp_enabled: id !== 'search', services_enabled: true, description: '', when_to_use: '', enabled: true, revision: 1, model_id: null, all_collections: false, all_repositories: false, collection_ids: [], repository_ids: [], service_ids: [], skill_ids: [], system_prompt: `${id} instructions`, model_name: null }));
  vi.mocked(profiles.getAgents).mockImplementation(async () => ({ agents: structuredClone(agents) }));
  vi.mocked(profiles.getAgent).mockImplementation(async id => structuredClone(agents.find(a => a.id === id)!));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({ test_chats: 0, documentation_sessions: 0, run_records: 0 });
});
function Detail() { const { id } = useParams(); return <AgentDetailView key={id} agentId={id!} />; }
function app(path: string) { return <MemoryRouter initialEntries={[path]}><AdminLayout><Routes><Route path="/agents" element={<AgentsView />} /><Route path="/agents/:id" element={<Detail />} /></Routes></AdminLayout></MemoryRouter>; }

test('catalog opens distinct agents with one model field and preserves SSH tasks across sections', async () => {
  const user = userEvent.setup();
  render(app('/agents'));
  await user.click(await screen.findByRole('link', { name: 'SSH agent' }));
  await screen.findByRole('combobox', { name: 'Model' });
  expect(screen.getByRole('switch', { name: 'Enable agent' })).toBeEnabled();
  expect(screen.getAllByRole('combobox', { name: 'Model', hidden: true })).toHaveLength(1);
  expect(screen.queryByRole('button', { name: 'Save model', hidden: true })).toBeNull();  // saved with the details
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  await user.type(screen.getByRole('textbox', { name: 'SSH task' }), 'Inspect disk usage');
  await user.click(screen.getByRole('tab', { name: 'General' }));
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  expect(screen.getByRole('textbox', { name: 'SSH task' })).toHaveValue('Inspect disk usage');
  await user.click(screen.getByRole('tab', { name: 'General' }));
  expect(await screen.findByRole('textbox', { name: 'System prompt' })).toHaveValue('operations instructions');
  await user.click(within(screen.getByRole('region', { name: 'Agent details' })).getByRole('link', { name: 'All agents' }));
  await user.click(await screen.findByRole('link', { name: 'Payments code' }));
  await screen.findByRole('combobox', { name: 'Model' });
  expect(screen.getAllByRole('combobox', { name: 'Model', hidden: true })).toHaveLength(1);
  await user.click(screen.getByRole('tab', { name: 'Test' }));
  expect(screen.getByText('SSH test custom-code')).toBeVisible();
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
  await screen.findByRole('combobox', { name: 'Model' });
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['General','Tools','Skills','Documents','Code projects','SSH','MCP','Services','Test']);
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
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({ test_chats: 2, documentation_sessions: 1, run_records: 3 });
  vi.mocked(profiles.deleteAgent).mockResolvedValue(undefined);
  render(app('/agents'));
  await user.click(await screen.findByRole('button', { name: 'Remove Payments code' }));
  const dialog = screen.getByRole('alertdialog');
  expect(await within(dialog).findByText('2 test chats, 1 documentation session, 3 run records')).toBeVisible();
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
  vi.mocked(profiles.getAgentHistory).mockResolvedValueOnce({ test_chats: 0, documentation_sessions: 0, run_records: 0 })
    .mockResolvedValue({ test_chats: 1, documentation_sessions: 0, run_records: 1 });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error('This agent has sessions or run history.'));
  render(app('/agents'));
  await user.click(await screen.findByRole('button', { name: 'Remove Payments code' }));
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove agent' }));
  expect(await within(screen.getByRole('alertdialog')).findByText('1 test chat, 1 run record')).toBeVisible();
  expect(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete agent and history' })).toBeEnabled();
});

const WORKING = 'This agent is working right now. Stop its documentation sessions and runs, or wait for them to finish, then delete it.';

test('a delete refused because the agent is working says so and keeps the dialog open', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.getAgentHistory).mockResolvedValue({ test_chats: 0, documentation_sessions: 1, run_records: 0 });
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
  vi.mocked(profiles.getAgentHistory).mockResolvedValueOnce({ test_chats: 0, documentation_sessions: 0, run_records: 0 })
    .mockResolvedValue({ test_chats: 0, documentation_sessions: 1, run_records: 0 });
  vi.mocked(profiles.deleteAgent).mockRejectedValueOnce(new Error(WORKING));
  render(app('/agents'));
  await user.click(await screen.findByRole('button', { name: 'Remove Payments code' }));
  await user.click(await within(screen.getByRole('alertdialog')).findByRole('button', { name: 'Remove agent' }));
  const dialog = screen.getByRole('alertdialog');
  expect(await within(dialog).findByText('1 documentation session')).toBeVisible();
  expect(within(dialog).getByText(WORKING)).toBeVisible();
});
