import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import DocumentsView from '../DocumentsView';
import DraftPageView from '../DraftPageView';

vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const NativeRequest = globalThis.Request;
// React Router's Node Request cannot consume jsdom's AbortSignal. Fetch is mocked in this suite.
beforeEach(() => vi.stubGlobal('Request', class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
}));
afterEach(() => vi.unstubAllGlobals());

const resources = { code_projects: [], services: [], servers: [], deployments: [], environments: [] };
const doc = { id: 'doc-1', slug: 'runbook', title: 'Runbook', description: 'How to run it', purpose: 'runbook', audience: 'both', tags: [],
  code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [], revision: 1, version: 1, current_version_id: 'v1',
  published_version_id: null, content_hash: 'a', created_at: '2026-10-12T10:00:00Z', updated_at: '2026-10-12T10:00:00Z', draft_count: 2 };
const newDraft = { id: 'd9', document_id: null, slug: 'disk-layout', folder: 'servers/web-1', title: 'Disk layout', origin: 'agent',
  session_id: 's1', session_title: 'Disk layout', author: null, edited: false, status: 'open', revision: 1, base_version_id: null,
  base_version: null, live_version: null, stale: false, created_at: '2026-10-12T10:00:00Z', updated_at: '2026-10-12T11:00:00Z' };
const detail = { ...newDraft, description: '', purpose: 'explanation', audience: 'both', tags: [], markdown: '# Disks', resource_ids: {},
  sources: [{ kind: 'read', source_document_id: null, source_version_id: null, source_title: 'Old overview', source_version: 4, deleted: true }],
  live_markdown: null, document_title: null, promoted_version_id: null, decided_at: null };

function api(routes: Record<string, (body: any) => [number, unknown]>) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url}`;
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ key, body });
    if (!(key in routes)) throw new Error(`unexpected ${key}`);
    const [status, value] = routes[key](body);
    return new Response(JSON.stringify(value), { status });
  }));
  return calls;
}
function show(url: string) {
  const router = createMemoryRouter([{ path: '/documents', element: <DocumentsView /> },
    { path: '/documents/drafts/:draftId', element: <DraftPageView /> }, { path: '/documents/:documentId', element: <p>Document page</p> }],
    { initialEntries: [url] });
  render(<RouterProvider router={router} />);
  return router;
}

test("the list shows each document's drafts, a Has drafts filter and the new-document drafts", async () => {
  const calls = api({
    'GET /api/documentation/documents?': () => [200, { documents: [doc], total: 1 }],
    'GET /api/documentation/documents?has_drafts=true': () => [200, { documents: [doc], total: 1 }],
    'GET /api/documentation/resources': () => [200, resources],
    'GET /api/documentation/drafts?new=true': () => [200, { drafts: [newDraft] }],
    'POST /api/documentation/drafts/d9/discard': () => [200, { ...detail, status: 'discarded' }],
  });
  show('/documents');
  expect(await screen.findByRole('link', { name: '2 drafts' })).toHaveAttribute('href', '/documents/doc-1?tab=drafts');
  const section = within(screen.getByRole('region', { name: 'New drafts' }));
  expect(section.getByRole('link', { name: 'Disk layout' })).toHaveAttribute('href', '/documents/drafts/d9');
  expect(section.getByText('servers/web-1')).toBeInTheDocument();
  expect(section.getByRole('link', { name: 'Agent run' })).toHaveAttribute('href', '/documentation/generations/s1');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Has drafts' }));
  await waitFor(() => expect(calls.some((c) => c.key === 'GET /api/documentation/documents?has_drafts=true')).toBe(true));
  fireEvent.click(section.getByRole('button', { name: 'Discard Disk layout' }));
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Discard draft' }));
  await waitFor(() => expect(calls.find((c) => c.key === 'POST /api/documentation/drafts/d9/discard')?.body).toEqual({ revision: 1 }));
});

test('a new-document draft page previews it, lists its sources and Promote creates the document', async () => {
  const calls = api({
    'GET /api/documentation/drafts/d9': () => [200, detail],
    'POST /api/documentation/drafts/d9/promote': () => [200, { draft: { ...detail, status: 'promoted', document_id: 'doc-9' },
      document_id: 'doc-9', version_id: 'v1', revision: 1, export_error: null }],
  });
  const router = show('/documents/drafts/d9');
  expect(await screen.findByText('# Disks')).toBeInTheDocument();
  expect(screen.getByText('Old overview v4 (deleted)')).toBeInTheDocument();
  expect(screen.queryByRole('tab', { name: 'Changes' })).toBeNull();  // nothing live to compare with
  fireEvent.click(screen.getByRole('button', { name: 'Promote' }));
  await waitFor(() => expect(router.state.location.pathname).toBe('/documents/doc-9'));
  expect(calls.find((c) => c.key === 'POST /api/documentation/drafts/d9/promote')!.body).toEqual({ revision: 1, confirm_stale: false });
});

test('Edit on the draft page saves the draft with its revision', async () => {
  let saved = false;
  const calls = api({
    'GET /api/documentation/drafts/d9': () => [200, saved ? { ...detail, markdown: '# Disks v2', revision: 2 } : detail],
    'PUT /api/documentation/drafts/d9': () => { saved = true; return [200, { ...detail, markdown: '# Disks v2', revision: 2, edited: true }]; },
  });
  show('/documents/drafts/d9');
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('Markdown'), { target: { value: '# Disks v2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  expect(await screen.findByText('Draft saved.')).toBeInTheDocument();
  // the edit form is gone; the preview shows the saved text (the form's textarea would match the text too)
  expect(await screen.findByRole('article')).toHaveTextContent('# Disks v2');
  expect(calls.find((c) => c.key === 'PUT /api/documentation/drafts/d9')!.body).toEqual({
    revision: 1, title: 'Disk layout', description: '', purpose: 'explanation', audience: 'both', tags: [], markdown: '# Disks v2' });
});

test('a refused promote shows why and reloads the draft, which may now revise an existing document', async () => {
  let converted = false;
  api({
    'GET /api/documentation/drafts/d9': () => [200, converted ? { ...detail, document_id: 'doc-3', slug: null, folder: null,
      base_version: 1, live_version: 1, live_markdown: '# Old', revision: 2 } : detail],
    'POST /api/documentation/drafts/d9/promote': () => { converted = true; return [409, { error: { message:
      'A document named disk-layout was created in this folder meanwhile. The draft is now a draft of it. Review it again.' } }]; },
  });
  show('/documents/drafts/d9');
  fireEvent.click(await screen.findByRole('button', { name: 'Promote' }));
  expect(await screen.findByText(/was created in this folder meanwhile/)).toBeInTheDocument();
  expect(await screen.findByRole('link', { name: 'Open it on its document' })).toHaveAttribute('href', '/documents/doc-3?tab=drafts&draft=d9');
  expect(screen.getByRole('tab', { name: 'Changes' })).toBeInTheDocument();
});

test('a stale promote keeps the warning, reloads the draft and sends confirm_stale on Promote anyway', async () => {
  const STALE = "This draft was based on version 1; the live version is now 2. Promoting replaces version 2's text.";
  let reloaded = false;
  const stale = { ...detail, id: 'd2', document_id: 'doc-1', slug: null, folder: null, base_version: 1, live_version: 1, live_markdown: '# v1', stale: false };
  const calls = api({
    'GET /api/documentation/drafts/d2': () => [200, reloaded ? { ...stale, live_version: 2, live_markdown: '# v2 live', stale: true } : stale],
    'POST /api/documentation/drafts/d2/promote': (body) => {
      if (body.confirm_stale) return [200, { draft: { ...stale, status: 'promoted' }, document_id: 'doc-1', version_id: 'v3', revision: 3, export_error: null }];
      reloaded = true; return [409, { error: { message: STALE, code: 'STALE_BASE' } }];
    },
  });
  show('/documents/drafts/d2');
  fireEvent.click(await screen.findByRole('button', { name: 'Promote' }));
  expect(await screen.findByText(STALE)).toBeInTheDocument();
  expect(await screen.findByText('stale')).toBeInTheDocument();
  expect(screen.getByText('# v2 live', { selector: 'del' })).toBeInTheDocument();
  expect(screen.getByText(STALE)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Promote anyway' }));
  await waitFor(() => expect(calls.filter((c) => c.key.endsWith('/promote')).map((c) => c.body))
    .toEqual([{ revision: 1, confirm_stale: false }, { revision: 1, confirm_stale: true }]));
});

test('a failed discard on the list shows why and refreshes the list so a retry works', async () => {
  let fails = true;
  const calls = api({
    'GET /api/documentation/documents?': () => [200, { documents: [doc], total: 1 }],
    'GET /api/documentation/resources': () => [200, resources],
    'GET /api/documentation/drafts?new=true': () => [200, { drafts: [fails ? newDraft : { ...newDraft, revision: 2 }] }],
    'POST /api/documentation/drafts/d9/discard': (body) => { fails = false; return body.revision === 1 ? [409, { error: { message: 'Changed meanwhile' } }] : [200, { ...detail, status: 'discarded' }]; },
  });
  show('/documents');
  const section = within(await screen.findByRole('region', { name: 'New drafts' }));
  fireEvent.click(section.getByRole('button', { name: /Discard/ }));
  fireEvent.click(await screen.findByRole('button', { name: 'Discard draft' }));
  expect(await screen.findByText('Changed meanwhile')).toBeInTheDocument();
  await waitFor(() => expect(calls.filter((c) => c.key === 'GET /api/documentation/drafts?new=true')).toHaveLength(2));
  expect(screen.getByText('Changed meanwhile')).toBeInTheDocument();
});

test("a promote whose export failed hands the export error to the document page", async () => {
  api({
    'GET /api/documentation/drafts/d9': () => [200, detail],
    'POST /api/documentation/drafts/d9/promote': () => [200, { draft: { ...detail, status: 'promoted', document_id: 'doc-9' },
      document_id: 'doc-9', version_id: 'v1', revision: 1, export_error: 'Saved. Writing the document file failed; use Export to folder to retry.' }],
  });
  const router = show('/documents/drafts/d9');
  fireEvent.click(await screen.findByRole('button', { name: 'Promote' }));
  await waitFor(() => expect(router.state.location.pathname).toBe('/documents/doc-9'));
  expect(router.state.location.state).toEqual(
    { notice: 'Draft promoted to version 1. Saved. Writing the document file failed; use Export to folder to retry.' });
});
