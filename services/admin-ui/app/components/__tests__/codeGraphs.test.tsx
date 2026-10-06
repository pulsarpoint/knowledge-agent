import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import CodeGraphsView from '../CodeGraphsView';
import type { CodeProject } from '../../lib/codeProjects';

vi.mock('../CodeGraphExplorer', () => ({ default: ({ projectId, sourceHash }: { projectId: string; sourceHash: string }) => <div>Explorer {projectId} {sourceHash}</div> }));

const base = { description: '', remote_url: null, requested_ref: null, clone_path: null, current_commit: null, checkout_path: null,
  has_token: false, synced_at: null, sync_status: null, sync_error: null, sync_interval_minutes: null, next_sync_at: null, revision: 1 };
const indexed: CodeProject = { ...base, id: 'p1', name: 'Payments', folder_paths: ['payments'], processing: { status: 'succeeded', error: null },
  code_index: { status: 'ready', source_hash: 'h1', source_file_count: 12, engine_version: '1.6.0',
    stats: { nodeCount: 340, edgeCount: 910, fileCount: 12, filesByLanguage: { python: 10, typescript: 2 } },
    unresolved_references: 3, parse_error_files: ['bad.py'], missing_folders: [], processed_at: '2026-10-07T10:00:00Z' } };
const missing: CodeProject = { ...base, id: 'p2', name: 'Billing', folder_paths: ['billing'], processing: null, code_index: null };
const running: CodeProject = { ...base, id: 'p3', name: 'Ledger', folder_paths: ['ledger'], processing: { status: 'running', error: null }, code_index: null };

function api(projects: CodeProject[]) {
  const fetch = vi.fn(async (raw: string, init?: RequestInit) => {
    const url = new URL(raw, 'http://localhost');
    if (init?.method === 'POST' && url.pathname.endsWith('/process')) return new Response(JSON.stringify({ job_id: 'job-1' }), { status: 202 });
    return new Response(JSON.stringify({ projects }), { status: 200 });
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

test('lists every project with its graph summary and opens a graph', async () => {
  const user = userEvent.setup(); api([indexed, missing, running]);
  render(<MemoryRouter><CodeGraphsView /></MemoryRouter>);
  const row = (await screen.findByRole('cell', { name: 'Payments' })).closest('tr')!;
  expect(row).toHaveTextContent('ready');
  expect(row).toHaveTextContent('12');
  expect(row).toHaveTextContent('340');
  expect(row).toHaveTextContent('910');
  expect(row).toHaveTextContent('python, typescript');
  expect(row).toHaveTextContent('1 parse error');
  const billing = screen.getByRole('cell', { name: 'Billing' }).closest('tr')!;
  expect(billing).toHaveTextContent('Not generated');
  expect(within(billing).queryByRole('button', { name: 'View graph for Billing' })).toBeNull();
  const ledger = screen.getByRole('cell', { name: 'Ledger' }).closest('tr')!;
  expect(ledger).toHaveTextContent('running');
  expect(within(ledger).getByRole('button', { name: 'Generate graph for Ledger' })).toBeDisabled();
  await user.click(within(row).getByRole('button', { name: 'View graph for Payments' }));
  expect(await screen.findByText('Explorer p1 h1')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Close graph' }));
  expect(screen.queryByText('Explorer p1 h1')).toBeNull();
});

test('Generate graph queues processing and reloads', async () => {
  const user = userEvent.setup(); const fetch = api([missing]);
  render(<MemoryRouter><CodeGraphsView /></MemoryRouter>);
  await user.click(await screen.findByRole('button', { name: 'Generate graph for Billing' }));
  await screen.findByText('Code graph generation queued for Billing.');
  expect(fetch.mock.calls.some(([url, init]) => String(url).endsWith('/api/code-projects/p2/process') && init?.method === 'POST')).toBe(true);
  expect(fetch.mock.calls.filter(([url]) => String(url).endsWith('/api/code-projects')).length).toBeGreaterThan(1);
});

test('empty state points to Projects', async () => {
  api([]);
  render(<MemoryRouter><CodeGraphsView /></MemoryRouter>);
  expect(await screen.findByText('No code projects yet')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Add a project' })).toHaveAttribute('href', '/code-projects');
});

test('a project in the URL opens its graph', async () => {
  api([indexed]);
  render(<MemoryRouter initialEntries={['/code-graphs?project=p1']}><CodeGraphsView /></MemoryRouter>);
  expect(await screen.findByText('Explorer p1 h1')).toBeInTheDocument();
});
