import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import AgentDetailView from '../AgentDetailView';
import PromptsPage from '../../routes/prompts';
import * as profiles from '../../lib/profiles';
import * as llm from '../../lib/llm';

vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgent: vi.fn(),
  getAgentOptions: vi.fn(), saveAgentPrompt: vi.fn() }));
vi.mock('../../lib/llm', async () => ({ ...await vi.importActual('../../lib/llm'), getLlmSettings: vi.fn() }));
vi.mock('../OperationsView', () => ({ default: () => null }));

const agent: profiles.AgentProfile = { id: 'custom', name: 'Payments analyst', description: '', when_to_use: '',
  enabled: true, runtime: 'openai', documents_enabled: false, repositories_enabled: false, ssh_enabled: false,
  skills_enabled: false, mcp_enabled: false, services_enabled: false, documents_grounded: false, revision: 4,
  model_id: null, all_collections: false, all_repositories: false, collection_ids: [], repository_ids: [],
  service_ids: [], skill_ids: [], system_prompt: 'Use evidence.' };

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ is_admin: true })));
  vi.mocked(profiles.getAgent).mockImplementation(async () => structuredClone(agent));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
  vi.mocked(llm.getLlmSettings).mockResolvedValue({ models: [], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
});
afterEach(() => vi.unstubAllGlobals());
function Detail() { const { id } = useParams(); return <AgentDetailView key={id} agentId={id!} />; }
const open = (path: string) => render(<MemoryRouter initialEntries={[path]}><Routes>
  <Route path="/agents/:id" element={<Detail />} /><Route path="/llm-settings/prompts" element={<PromptsPage />} />
</Routes></MemoryRouter>);

test('the agent page has no Prompts or Versions tab', async () => {
  open('/agents/custom');
  await screen.findByRole('heading', { name: 'Payments analyst', level: 2 });
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(
    ['General', 'Tools', 'Skills', 'Documents', 'Code projects', 'SSH', 'MCP', 'Services', 'Test']);
});

test('General edits and saves the prompt with the agent revision', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.saveAgentPrompt).mockImplementation(async (id, revision, text) => ({ ...structuredClone(agent), id, revision: revision + 1, system_prompt: text }));
  open('/agents/custom');
  const prompt = await screen.findByRole('textbox', { name: 'System prompt' });
  expect(prompt).toHaveValue('Use evidence.');
  expect(screen.getByRole('button', { name: 'Save prompt' })).toBeDisabled();
  await user.type(prompt, ' Cite sources.');
  await user.click(screen.getByRole('button', { name: 'Save prompt' }));
  expect(await screen.findByText('System prompt saved.')).toBeVisible();
  expect(profiles.saveAgentPrompt).toHaveBeenCalledWith('custom', 4, 'Use evidence. Cite sources.');
  expect(screen.getByRole('button', { name: 'Save prompt' })).toBeDisabled();
});

test('a refused save keeps the edit and says why', async () => {
  const user = userEvent.setup();
  vi.mocked(profiles.saveAgentPrompt).mockRejectedValue(new Error('Agent changed. Reload before saving.'));
  open('/agents/custom');
  const prompt = await screen.findByRole('textbox', { name: 'System prompt' });
  await user.clear(prompt);
  await user.type(prompt, 'Shorter.');
  await user.click(screen.getByRole('button', { name: 'Save prompt' }));
  expect(await screen.findByText('Agent changed. Reload before saving.')).toBeVisible();
  expect(prompt).toHaveValue('Shorter.');
});

test('old prompt links open the agent General tab', async () => {
  open('/llm-settings/prompts?agent=code');
  expect(await screen.findByRole('tab', { name: 'General' })).toHaveAttribute('aria-selected', 'true');
  expect(await screen.findByRole('textbox', { name: 'System prompt' })).toHaveValue('Use evidence.');
});
