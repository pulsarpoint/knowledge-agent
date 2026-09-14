import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import PromptsView from '../PromptsView';
import * as api from '../../lib/prompts';
import { ApiError } from '../../lib/api';

vi.mock('../../lib/prompts', async () => ({ ...await vi.importActual('../../lib/prompts'), getPrompts: vi.fn(), savePrompt: vi.fn(), activatePrompt: vi.fn(), deletePrompt: vi.fn() }));

const original: api.PromptVersion = { id: 'p1', agent: 'search', version: 1, title: 'Default search', content: 'Answer from evidence.', revision: 1, status: 'active', created_at: '2026-09-09T10:00:00Z', updated_at: '2026-09-09T10:00:00Z' };
let data: api.Prompts;
beforeEach(() => {
  vi.clearAllMocks();
  data = { agents: [{ agent: 'search', active_version_id: 'p1', revision: 1 }], versions: [{ ...original }] };
  vi.mocked(api.getPrompts).mockImplementation(async () => structuredClone(data));
  vi.mocked(api.savePrompt).mockImplementation(async (agent, title, content) => {
    const saved: api.PromptVersion = { ...original, id: 'p2', version: 2, agent, title, content, status: 'draft' };
    data.versions.unshift(saved);
    return saved;
  });
  vi.mocked(api.activatePrompt).mockImplementation(async version => {
    data.agents[0] = { ...data.agents[0], active_version_id: version.id, revision: 2 };
    data.versions = data.versions.map(row => ({ ...row, status: row.id === version.id ? 'active' : 'archived' }));
    return data.versions.find(row => row.id === version.id)!;
  });
});

test('new versions stay draft until activation is explicitly confirmed', async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><PromptsView agent="search" /></MemoryRouter>);
  await user.click(await screen.findByRole('button', { name: 'New draft from this version' }));
  expect(screen.queryByRole('button', { name: 'Set active' })).toBeNull();
  await user.clear(screen.getByLabelText('Version title'));
  await user.type(screen.getByLabelText('Version title'), 'Concise responses');
  await user.clear(screen.getByLabelText('System prompt'));
  await user.type(screen.getByLabelText('System prompt'), 'Use concise prose. Diagrams are optional.');
  await user.click(screen.getByRole('button', { name: 'Save draft' }));
  expect(await screen.findByText('Version 2 saved as draft. The active prompt has not changed.')).toBeInTheDocument();
  expect(api.activatePrompt).not.toHaveBeenCalled();
  expect(data.agents[0].active_version_id).toBe('p1');
  await user.click(screen.getByRole('button', { name: 'Set active' }));
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Cancel' }));
  expect(api.activatePrompt).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Set active' }));
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Activate version' }));
  expect(await screen.findByText('Version 2 is now active.')).toBeInTheDocument();
  expect(data.agents[0].active_version_id).toBe('p2');
  expect(screen.queryByRole('button', { name: 'Edit draft' })).toBeNull();
});

test('published content is read-only and failed saves preserve local edits', async () => {
  const user = userEvent.setup();
  vi.mocked(api.savePrompt).mockRejectedValue(new Error('offline'));
  render(<MemoryRouter><PromptsView agent="search" /></MemoryRouter>);
  expect(await screen.findByText('Answer from evidence.')).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).toBeNull();
  await user.click(screen.getByRole('button', { name: 'New draft from this version' }));
  await user.type(screen.getByLabelText('System prompt'), ' Keep my changes.');
  await user.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(screen.getByText('Could not update prompts. Try reloading.')).toBeInTheDocument());
  expect(screen.getByLabelText('System prompt')).toHaveValue('Answer from evidence. Keep my changes.');
  expect(api.activatePrompt).not.toHaveBeenCalled();
});

test.each(['draft', 'archived'] as const)('delete %s requires confirmation and returns the preview to the active version', async status => {
  const user = userEvent.setup();
  const inactive: api.PromptVersion = { ...original, id: 'p2', version: 2, title: 'Old instructions', content: 'Old content.', status };
  data.versions.unshift(inactive);
  vi.mocked(api.deletePrompt).mockResolvedValue(undefined);
  render(<MemoryRouter><PromptsView agent="search" /></MemoryRouter>);
  await user.click(await screen.findByRole('button', { name: 'View version 2' }));
  expect(screen.queryByRole('button', { name: 'Delete version 1' })).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Delete version 2' }));
  expect(within(screen.getByRole('alertdialog')).getByText(/Old instructions/)).toBeInTheDocument();
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Cancel' }));
  expect(api.deletePrompt).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'View version 2' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Delete version 2' }));
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete version' }));
  expect(await screen.findByText('Version 2 deleted.')).toBeInTheDocument();
  expect(api.deletePrompt).toHaveBeenCalledTimes(1);
  expect(api.deletePrompt).toHaveBeenCalledWith(inactive, data.agents[0]);
  expect(screen.queryByRole('button', { name: 'View version 2' })).toBeNull();
  expect(screen.getByText('Answer from evidence.')).toBeInTheDocument();
  expect(api.activatePrompt).not.toHaveBeenCalled();
});

test('a stale deletion leaves the version and confirmation visible', async () => {
  const user = userEvent.setup();
  data.versions.unshift({ ...original, id: 'p2', version: 2, title: 'Old instructions', status: 'archived' });
  vi.mocked(api.deletePrompt).mockRejectedValue(new ApiError('The active prompt version cannot be deleted.', 409));
  render(<MemoryRouter><PromptsView agent="search" /></MemoryRouter>);
  await user.click(await screen.findByRole('button', { name: 'Delete version 2' }));
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete version' }));
  expect(await within(screen.getByRole('alertdialog')).findByText('The active prompt version cannot be deleted.')).toBeInTheDocument();
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Cancel' }));
  expect(screen.getByRole('button', { name: 'View version 2' })).toBeInTheDocument();
});
