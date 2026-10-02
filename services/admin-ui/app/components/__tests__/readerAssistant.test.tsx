import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, expect, test, vi } from 'vitest';
import ReaderAssistantCard from '../ReaderAssistantCard';
import GroundingSources from '../GroundingSources';

afterEach(() => vi.unstubAllGlobals());
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const settings = { knowledge_agent_id: 'search', revision: 1, updated_at: null, candidates: [
  { id: 'search', name: 'Search agent', enabled: true, violations: [] },
  { id: 'operations', name: 'SSH agent', enabled: true, violations: ['uses SSH'] },
  { id: 'docs', name: 'Docs helper', enabled: true, violations: [] },
] };

test('shows the Reader agent, disables violating agents and saves a new choice', async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
    init?.method === 'PUT' ? json({ ...settings, knowledge_agent_id: 'docs', revision: 2 }) : json(settings));
  vi.stubGlobal('fetch', fetchMock);
  render(<MemoryRouter><ReaderAssistantCard /></MemoryRouter>);
  const select = await screen.findByLabelText('Reader agent');
  expect(screen.getByRole('option', { name: /SSH agent — uses SSH/ })).toBeDisabled();
  await userEvent.selectOptions(select, 'docs');
  await userEvent.click(screen.getByRole('button', { name: 'Save Reader agent' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/reader/settings', expect.objectContaining({
    method: 'PUT', body: JSON.stringify({ knowledge_agent_id: 'docs', revision: 1 }) })));
  expect(await screen.findByText('Saved.')).toBeInTheDocument();
});

test('renders nothing when settings cannot be read', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ is_admin: true })));
  const { container } = render(<MemoryRouter><ReaderAssistantCard /></MemoryRouter>);
  await waitFor(() => expect(container).toBeEmptyDOMElement());
});

test('grounding sources list cited documents', () => {
  render(<GroundingSources sources={[{ n: 1, path: 'payments/retries.md', heading_path: 'payments', anchor: null }]} />);
  expect(screen.getByText('[1]')).toBeInTheDocument();
  expect(screen.getByText('payments/retries.md')).toBeInTheDocument();
});
