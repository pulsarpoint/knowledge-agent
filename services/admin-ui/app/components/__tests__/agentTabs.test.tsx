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

vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgents: vi.fn(), getAgent: vi.fn(), getAgentOptions: vi.fn(), saveAgent: vi.fn(), deleteAgent: vi.fn() }));
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
  agents = ['search', 'operations', 'code', 'custom-code'].map(id => ({ id, is_coordinator: false, documents_grounded: false, name: id === 'custom-code' ? 'Payments code' : prompts.agentNames[id as prompts.AgentName], documents_enabled: id === 'search', repositories_enabled: id !== 'operations', ssh_enabled: id === 'operations', skills_enabled: true, mcp_enabled: id !== 'search', services_enabled: true, description: '', when_to_use: '', enabled: true, revision: 1, model_id: null, all_collections: false, all_repositories: false, collection_ids: [], repository_ids: [], service_ids: [], skill_ids: [], active_prompt: null, model_name: null, prompt_title: `${id} prompt`, prompt_version: 3 }));
  data = {
    agents: agents.map(a => ({ agent: a.id, active_version_id: a.id, revision: 1 })),
    versions: agents.map(a => ({ agent: a.id, id: a.id, version: 3, title: `${a.id} prompt`, content: `${a.id} instructions`, revision: 1, status: 'active', created_at: '', updated_at: '' })),
  };
  vi.mocked(prompts.getPrompts).mockImplementation(async () => structuredClone(data));
  vi.mocked(profiles.getAgents).mockImplementation(async () => ({ agents: structuredClone(agents) }));
  vi.mocked(profiles.getAgent).mockImplementation(async id => structuredClone(agents.find(a => a.id === id)!));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
});
function Detail() { const { id } = useParams(); return <AgentDetailView key={id} agentId={id!} />; }
function app(path: string) { return <MemoryRouter initialEntries={[path]}><AdminLayout><Routes><Route path="/agents" element={<AgentsView />} /><Route path="/agents/:id" element={<Detail />} /><Route path="/llm-settings/prompts" element={<PromptsPage />} /></Routes></AdminLayout></MemoryRouter>; }

test('coordinator is protected in the list and edits notes and prompt versions on its agent page', async () => {
  const coordinator = { ...agents[0], id: 'coordinator', name: 'Board coordinator', is_coordinator: true,
    documents_enabled: false, repositories_enabled: false, ssh_enabled: false, skills_enabled: false,
    mcp_enabled: false, services_enabled: false };
  agents.unshift(coordinator);
  data.agents.push({ agent: coordinator.id, active_version_id: 'coordinator-prompt', revision: 1 });
  data.versions.push({ ...data.versions[0], agent: coordinator.id, id: 'coordinator-prompt',
    title: 'Coordinator prompt', content: 'Delegate and review evidence.' });
  const settings = { agent_id: coordinator.id, name: coordinator.name, enabled: true, notes: 'Prioritize evidence.',
    model_id: null, model_name: null, model_enabled: null, system_prompt: 'Delegate and review evidence.',
    summary_prompt: 'Write a final response from the evidence.', max_turns: 8, max_subtasks: 20, timeout_seconds: 1800, revision: 1 };
  const saves: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/agent-board/settings') {
      if (init?.method === 'PUT') {
        const body = JSON.parse(init.body as string); saves.push(body);
        return Response.json({ ...settings, ...body, revision: 2 });
      }
      return Response.json(settings);
    }
    return Response.json({ is_admin: true });
  }));
  const user = userEvent.setup();
  render(app('/agents'));
  const row = (await screen.findByRole('link', { name: 'Board coordinator' })).closest('tr')!;
  expect(within(row).getByText('Coordinator')).toBeVisible();
  expect(within(row).queryByRole('button', { name: 'Remove Board coordinator' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Remove Code search agent' })).toBeEnabled();
  await user.click(within(row).getByRole('link', { name: 'Board coordinator' }));
  const notes = await screen.findByLabelText('Notes and additional instructions');
  expect(screen.queryByRole('switch', { name: 'Enable agent' })).not.toBeInTheDocument();
  expect(screen.getByText('The coordinator is always enabled.')).toBeVisible();
  expect(notes).toHaveValue('Prioritize evidence.');
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['General', 'Prompts']);
  expect(screen.getByRole('link', { name: 'Open multi-agent board' })).toHaveAttribute('href', '/agent-board');
  fireEvent.change(notes, { target: { value: 'Check deployment evidence first.' } });
  fireEvent.change(screen.getByLabelText('Maximum subagent calls per task'), { target: { value: '4' } });
  fireEvent.change(screen.getByLabelText('Maximum model turns'), { target: { value: '6' } });
  fireEvent.change(screen.getByLabelText('Task timeout (seconds)'), { target: { value: '120' } });
  await user.click(screen.getByRole('tab', { name: 'Prompts' }));
  expect(await screen.findByText('Coordinator prompt')).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'General' }));
  expect(screen.getByLabelText('Notes and additional instructions')).toHaveValue('Check deployment evidence first.');
  await user.clear(screen.getByLabelText('Final summary prompt'));
  await user.type(screen.getByLabelText('Final summary prompt'), 'Summarize with citations.');
  await user.click(screen.getByRole('button', { name: 'Save coordinator settings' }));
  expect(await screen.findByRole('status')).toHaveTextContent('Coordinator settings saved');
  expect(saves).toEqual([{ notes:'Check deployment evidence first.', summary_prompt:'Summarize with citations.', max_turns:6, max_subtasks:4, timeout_seconds:120, revision:1 }]);
});

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

test('agents are created only through the wizard', async () => {
  render(app('/agents'));
  expect(await screen.findByRole('button', { name: 'New Agent Wizard' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Create agent' })).toBeNull();
  expect(screen.queryByLabelText('System prompt')).toBeNull();
});

test.each(['search','operations','code','custom-code'])('every agent has the same capability tabs: %s', async id => {
  render(app(`/agents/${id}`));
  await screen.findByRole('combobox', { name: 'Model connection' });
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['General','Skills','Documents','Code projects','SSH','MCP','Services','Prompts','Versions','Test']);
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
  expect(profiles.deleteAgent).toHaveBeenCalledWith('custom-code', 1);
  expect(screen.getByRole('alertdialog')).toBeVisible();
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove agent' }));
  expect(screen.queryByRole('button', { name: 'Remove Payments code' })).not.toBeInTheDocument();
});
