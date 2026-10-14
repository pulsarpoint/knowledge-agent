import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import DocumentEditorView from '~/features/documents/DocumentEditorView';
import DocumentsView from '~/features/documents/DocumentsView';
import { DocumentationPromptEditorView } from '~/features/documents/DocumentationPromptsView';

vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const NativeRequest = globalThis.Request;
// React Router's Node Request cannot consume jsdom's AbortSignal. Fetch is mocked in this suite.
beforeEach(() => vi.stubGlobal('Request', class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
}));
afterEach(() => vi.unstubAllGlobals());
const resources = { code_projects: [{ id:'repo-1',name:'Payments code' }], services:[{ id:'service-1',name:'Payments API' }], servers:[{ id:'server-1',name:'Production host' }], deployments:[{ id:'deployment-1',name:'Payments API / Production host / prod',service_id:'service-1',server_id:'server-1' }] };
const first = { id:'v1',version:1,title:'Payments runbook',description:'Original description',purpose:'runbook',audience:'both',tags:[],markdown:'# Original',content_hash:'a'.repeat(64),source_snapshot:{ ...resources, code_projects:[{ ...resources.code_projects[0], git_commit:'a'.repeat(40) }] },origin:'manual',base_version_id:null,created_at:'2026-09-13T10:00:00Z' };
const document = { id:'doc-1',slug:'payments-runbook',title:'Payments runbook',description:'Current description',purpose:'runbook',audience:'both',tags:[],code_project_ids:['repo-1'],service_ids:[],server_ids:[],deployment_ids:[],revision:2,version:2,current_version_id:'v2',published_version_id:null,current_version:{ ...first,id:'v2',version:2,markdown:'# Current', source_snapshot:{ ...resources, code_projects:[{ ...resources.code_projects[0], git_commit:'b'.repeat(40) }] } },versions:[{ ...first,id:'v2',version:2 },first],updated_at:'2026-09-13T11:00:00Z' };
const response = (value: unknown,status=200) => new Response(JSON.stringify(value),{ status });
function setup(url: string, saveStatus=200) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path=String(input);
    if (init?.method === 'PUT' || init?.method === 'POST') return saveStatus === 200 ? response({ id:'doc-1',revision:3,version_id:'v3' }) : response({ error:{ message:'This document changed. Reload before saving.' } },saveStatus);
    if (init?.method === 'DELETE') return response({ id:'doc-1' });
    if (path.endsWith('/resources')) return response(resources);
    if (path.includes('/versions/')) return response(first);
    if (path.includes('/drafts')) return response({ drafts: [] });
    if (path.includes('/documents?')) return response({ documents:[document],total:1 });
    return response(document);
  });
  vi.stubGlobal('fetch',fetch);
  const router=createMemoryRouter([
    { path:'/documents',element:<DocumentsView /> },{ path:'/documents/new',element:<DocumentEditorView /> },{ path:'/documents/:documentId',element:<DocumentEditorView /> },
  ],{ initialEntries:[url] });
  render(<RouterProvider router={router} />);
  return { fetch,router };
}
function tab(name: string) { fireEvent.mouseDown(screen.getByRole('tab',{ name }), { button:0,ctrlKey:false }); }

test('creates a document with multiple resource types and deployment context',async () => {
  const { fetch }=setup('/documents/new');
  fireEvent.change(await screen.findByLabelText('Title'),{ target:{ value:'New runbook' } });
  expect(screen.getByLabelText('Slug')).toHaveValue('new-runbook');
  fireEvent.change(screen.getByLabelText('Markdown'),{ target:{ value:'# How to investigate' } });
  tab('Linked resources');
  fireEvent.click(await screen.findByRole('checkbox',{ name:'Payments code' }));
  fireEvent.click(screen.getByRole('checkbox',{ name:'Payments API / Production host / prod' }));
  expect(screen.getByRole('checkbox',{ name:'Payments API' })).toBeChecked();
  expect(screen.getByRole('checkbox',{ name:'Production host' })).toBeChecked();
  fireEvent.click(screen.getByRole('button',{ name:'Create document' }));
  await waitFor(() => expect(fetch.mock.calls.some(([,init]) => init?.method === 'POST')).toBe(true));
  const submitted=JSON.parse(fetch.mock.calls.find(([,init]) => init?.method === 'POST')![1]!.body as string);
  expect(submitted).toMatchObject({ slug:'new-runbook',markdown:'# How to investigate',code_project_ids:['repo-1'],service_ids:['service-1'],server_ids:['server-1'],deployment_ids:['deployment-1'] });
});

test('previews a saved version and loads it as an unsaved draft without changing history',async () => {
  const { fetch }=setup('/documents/doc-1');
  await screen.findByLabelText('Title');
  tab('Version history');
  fireEvent.click(await screen.findByRole('button',{ name:/Version 1/ }));
  expect(await screen.findByText('# Original')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{ name:'Use version as draft' }));
  expect(await screen.findByLabelText('Markdown')).toHaveValue('# Original');
  expect(screen.getByLabelText('Short description')).toHaveValue('Original description');
  expect(fetch.mock.calls.some(([,init]) => init?.method === 'PUT')).toBe(false);
  fireEvent.click(screen.getByRole('button',{ name:'Save new version' }));
  await waitFor(() => expect(fetch.mock.calls.some(([,init]) => init?.method === 'PUT')).toBe(true));
  expect(JSON.parse(fetch.mock.calls.find(([,init]) => init?.method === 'PUT')![1]!.body as string)).toMatchObject({ revision:2,markdown:'# Original',source_commits:{'repo-1':'a'.repeat(40)} });
});

test('failed save retains edits and navigating away requires confirmation',async () => {
  setup('/documents/doc-1',409);
  await screen.findByLabelText('Title');
  fireEvent.change(screen.getByLabelText('Markdown'),{ target:{ value:'# Unsaved work' } });
  fireEvent.click(screen.getByRole('button',{ name:'Save new version' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('This document changed');
  expect(screen.getByLabelText('Markdown')).toHaveValue('# Unsaved work');
  fireEvent.click(screen.getByRole('link',{ name:'Documents' }));
  const dialog=await screen.findByRole('alertdialog');
  fireEvent.click(within(dialog).getByRole('button',{ name:'Keep editing' }));
  expect(screen.getByLabelText('Markdown')).toHaveValue('# Unsaved work');
});

test('document deletion requires confirmation and Cancel never sends DELETE',async () => {
  const { fetch }=setup('/documents/doc-1');
  fireEvent.click(await screen.findByRole('button',{ name:'Delete' }));
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button',{ name:'Cancel' }));
  expect(fetch.mock.calls.some(([,init]) => init?.method === 'DELETE')).toBe(false);
  fireEvent.click(screen.getByRole('button',{ name:'Delete' }));
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button',{ name:'Delete document' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/documentation/documents/doc-1?revision=2&confirmed=true',{ method:'DELETE' }));
});

test('prompt edits create drafts and active versions cannot be deleted',async () => {
  const prompt={ id:'prompt-1',name:'Architecture',description:'Analyze code',tags:['documentation','code'],enabled:true,revision:3,active_version_id:'p1',versions:[{ id:'p2',version:2,content:'Draft instructions',activated_at:null,created_at:first.created_at },{ id:'p1',version:1,content:'Active instructions',activated_at:first.created_at,created_at:first.created_at }] };
  const fetch=vi.fn(async (_input: RequestInfo | URL,init?: RequestInit) => init?.method === 'PUT' ? response({ id:'prompt-1',revision:4,version_id:'p3' }) : response(prompt));
  vi.stubGlobal('fetch',fetch);
  const router=createMemoryRouter([{ path:'/prompts/:promptId',element:<DocumentationPromptEditorView /> }],{ initialEntries:['/prompts/prompt-1'] });
  render(<RouterProvider router={router} />);
  await screen.findByLabelText('Name');
  expect(screen.getByRole('button',{ name:'Delete v1' })).toBeDisabled();
  expect(screen.getByText('Draft',{ exact:true })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Prompt text'),{ target:{ value:'Improved instructions' } });
  expect(screen.getByRole('button',{ name:'Activate v2' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{ name:'Save prompt' }));
  await waitFor(() => expect(fetch.mock.calls.some(([,init]) => init?.method === 'PUT')).toBe(true));
  const saved=JSON.parse(fetch.mock.calls.find(([,init]) => init?.method === 'PUT')![1]!.body as string);
  expect(saved).toMatchObject({ revision:3,content:'Improved instructions' });
  expect(fetch.mock.calls.some(([,init]) => init?.method === 'POST')).toBe(false);
});
