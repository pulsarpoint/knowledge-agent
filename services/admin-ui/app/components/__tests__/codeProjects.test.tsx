import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CodeProjectsView from '../CodeProjectsView';
import type { CodeProject } from '../../lib/codeProjects';

const project: CodeProject = { id:'code-1', name:'Payments', description:'Payment sources', remote_url:'https://github.com/example/payments.git',
  requested_ref:null, clone_path:'payments', folder_paths:['payments'], current_commit:'a'.repeat(40), checkout_path:'payments', has_token:true,
  synced_at:null, sync_status:null, sync_error:null, sync_interval_minutes:null, next_sync_at:null, revision:3 };

function api(projects: CodeProject[] = []) {
  const fetch = vi.fn(async (raw: string, init?: RequestInit) => {
    const url = new URL(raw, 'http://localhost');
    let body: unknown;
    if (init?.method === 'POST' || init?.method === 'PUT') body = { id:'code-1', job_id:'job-1' };
    else if (url.pathname === '/api/code-projects') body = { projects };
    else if (url.pathname === '/api/rag/collections') body = { collections: [{ name: 'payments-docs' }] };
    else if (url.pathname === '/api/code/file') body = { path:url.searchParams.get('path'), text:'print("source code")' };
    else {
      const path = url.searchParams.get('path') ?? '';
      body = { root:'/work/code', host_path:'/host/code', path, total:2, next_offset:null,
        entries: path ? [{name:'app.py',path:`${path}/app.py`,kind:'file',size:20}] : [
          {name:'api',path:'api',kind:'folder',size:0}, {name:'shared',path:'shared',kind:'folder',size:0},
          {name:'escape',path:'escape',kind:'symlink',size:0}] };
      if (url.searchParams.get('folders_only') === 'true') {
        const children = path === '' ? ['api','shared','payments'] : path === 'api' ? ['src'] : path === 'api/src' ? ['billing'] : [];
        body = { root:'/work/code', host_path:'/host/code', path, total:children.length, next_offset:null,
          entries:children.map(name => ({ name, path:path ? `${path}/${name}` : name, kind:'folder', size:0 })) };
      }
    }
    return new Response(JSON.stringify(body), {status:200});
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

test('maps multiple existing folders into a local code project', async () => {
  const fetch = api(); render(<CodeProjectsView />);
  await screen.findByText('/host/code → /work/code');
  fireEvent.click(screen.getByRole('button',{name:'Add project'}));
  fireEvent.change(screen.getByLabelText('Project name'), {target:{value:'Local code'}});
  expect(screen.getByRole('button',{name:'Save project'})).toBeDisabled();
  fireEvent.click(await screen.findByRole('checkbox',{name:'Select folder api'}));
  fireEvent.click(screen.getByRole('checkbox',{name:'Select folder shared'}));
  fireEvent.click(screen.getByRole('button',{name:'Save project'}));
  await screen.findByText('Code project saved.');
  const body = JSON.parse(fetch.mock.calls.find(([,init]) => init?.method==='POST')![1]!.body as string);
  expect(body).toMatchObject({ name:'Local code', remote_url:null, folder_paths:['api','shared'], clone_now:false });
  expect(body).not.toHaveProperty('token');
});

test('Git source asks for clone folder, cloning choice and automatic pull interval', async () => {
  const user = userEvent.setup(); const fetch = api(); render(<CodeProjectsView />);
  await user.click(screen.getByRole('button',{name:'Add project'}));
  await user.type(screen.getByLabelText('Project name'), 'Remote code');
  await user.click(screen.getByRole('combobox',{name:'Source'}));
  await user.click(screen.getByRole('option',{name:'Git repository'}));
  expect(screen.getByLabelText('Clone folder')).toBeRequired();
  expect(screen.getByLabelText('Clone folder')).toHaveValue('Remote code');
  fireEvent.change(screen.getByLabelText('Project name'), {target:{value:'Payment API'}});
  expect(screen.getByLabelText('Clone folder')).toHaveValue('Payment API');
  expect(screen.getByRole('checkbox',{name:'Clone repository after saving'})).toBeChecked();
  fireEvent.change(screen.getByLabelText('Git repository URL'), {target:{value:'example/remote'}});
  fireEvent.change(screen.getByLabelText('Clone folder'), {target:{value:'remote-code'}});
  fireEvent.change(screen.getByLabelText('Project name'), {target:{value:'Renamed API'}});
  expect(screen.getByLabelText('Clone folder')).toHaveValue('remote-code');
  await user.click(screen.getByRole('switch',{name:'Automatic pulls'}));
  fireEvent.change(screen.getByLabelText('Pull interval (minutes)'), {target:{value:'15'}});
  await user.click(screen.getByRole('button',{name:'Save and clone'}));
  await screen.findByText('Code project saved. Clone queued.');
  const body = JSON.parse(fetch.mock.calls.find(([,init]) => init?.method==='POST')![1]!.body as string);
  expect(body).toMatchObject({ remote_url:'example/remote', clone_path:'remote-code', clone_now:true, sync_interval_minutes:15 });
});

test('can save a Git project without cloning it', async () => {
  const user = userEvent.setup(); const fetch = api(); render(<CodeProjectsView />);
  await user.click(screen.getByRole('button',{name:'Add project'}));
  fireEvent.change(screen.getByLabelText('Project name'), {target:{value:'Later'}});
  await user.click(screen.getByRole('combobox',{name:'Source'}));
  await user.click(screen.getByRole('option',{name:'Git repository'}));
  fireEvent.change(screen.getByLabelText('Git repository URL'), {target:{value:'example/later'}});
  fireEvent.change(screen.getByLabelText('Clone folder'), {target:{value:'later'}});
  await user.click(screen.getByRole('checkbox',{name:'Clone repository after saving'}));
  await user.click(screen.getByRole('button',{name:'Save project'}));
  await screen.findByText('Code project saved.');
  const body = JSON.parse(fetch.mock.calls.find(([,init]) => init?.method==='POST')![1]!.body as string);
  expect(body.clone_now).toBe(false);
  expect(fetch.mock.calls.filter(([url]) => url.endsWith('/pull'))).toHaveLength(0);
});

test('browses folders and previews file contents without following symlinks', async () => {
  const fetch = api(); render(<CodeProjectsView />);
  expect(await screen.findByRole('button',{name:'escape'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'api'}));
  fireEvent.click(await screen.findByRole('button',{name:'app.py'}));
  expect(await screen.findByLabelText('Code file content')).toHaveTextContent('print("source code")');
  expect(fetch.mock.calls.some(([url]) => url === '/api/code/file?path=api%2Fapp.py')).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Code root'}));
  expect(await screen.findByRole('button',{name:'shared'})).toBeVisible();
  expect(screen.queryByLabelText('Code file content')).not.toBeInTheDocument();
});

test('editing preserves hidden credentials unless removal is explicit and sends the revision', async () => {
  const fetch = api([project]); render(<CodeProjectsView />);
  fireEvent.click(await screen.findByRole('button',{name:'Edit Payments'}));
  expect(screen.getByLabelText('Git repository URL')).toBeDisabled();
  expect(screen.getByLabelText('Clone folder')).toBeDisabled();
  expect(screen.getByRole('switch',{name:'Private repository'})).toBeChecked();
  for (const label of ['Git username', 'Access token']) {
    expect(screen.getByLabelText(label)).toHaveAttribute('type','password');
    expect(screen.getByLabelText(label)).toHaveValue('');
    expect(screen.getByLabelText(label)).not.toBeRequired();
  }
  fireEvent.click(screen.getByRole('button',{name:'Save project'}));
  await screen.findByText('Code project saved.');
  let bodies = fetch.mock.calls.filter(([,init]) => init?.method==='PUT').map(([,init]) => JSON.parse(init!.body as string));
  expect(bodies[0]).toMatchObject({revision:3,clone_now:false,folder_paths:['payments']});
  expect(bodies[0]).not.toHaveProperty('token');
  expect(bodies[0]).not.toHaveProperty('username');
  fireEvent.click(screen.getByRole('button',{name:'Edit Payments'}));
  fireEvent.click(screen.getByRole('switch',{name:'Private repository'}));
  expect(screen.queryByLabelText('Access token')).not.toBeInTheDocument();
  expect(screen.getByText(/Saving will remove the saved username and token/)).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'Save project'}));
  await waitFor(() => expect(fetch.mock.calls.filter(([,init]) => init?.method==='PUT')).toHaveLength(2));
  bodies = fetch.mock.calls.filter(([,init]) => init?.method==='PUT').map(([,init]) => JSON.parse(init!.body as string));
  expect(bodies[1].token).toBeNull();
  expect(bodies[1]).not.toHaveProperty('username');
});

test('new private repositories require credentials and submit them separately from the URL', async () => {
  const user = userEvent.setup(); const fetch = api(); render(<CodeProjectsView />);
  await user.click(screen.getByRole('button',{name:'Add project'}));
  fireEvent.change(screen.getByLabelText('Project name'), {target:{value:'Private code'}});
  await user.click(screen.getByRole('combobox',{name:'Source'}));
  await user.click(screen.getByRole('option',{name:'Git repository'}));
  fireEvent.change(screen.getByLabelText('Git repository URL'), {target:{value:project.remote_url}});
  expect(screen.queryByLabelText('Access token')).not.toBeInTheDocument();
  await user.click(screen.getByRole('switch',{name:'Private repository'}));
  for (const label of ['Git username', 'Access token']) {
    expect(screen.getByLabelText(label)).toBeRequired();
    expect(screen.getByLabelText(label)).toHaveAttribute('type','password');
  }
  await user.click(screen.getByRole('button',{name:'Save and clone'}));
  expect(fetch.mock.calls.some(([,init]) => init?.method==='POST')).toBe(false);
  fireEvent.change(screen.getByLabelText('Git username'), {target:{value:'private-user'}});
  fireEvent.change(screen.getByLabelText('Access token'), {target:{value:'private-token'}});
  await user.click(screen.getByRole('button',{name:'Save and clone'}));
  await screen.findByText('Code project saved. Clone queued.');
  const body = JSON.parse(fetch.mock.calls.find(([,init]) => init?.method==='POST')![1]!.body as string);
  expect(body).toMatchObject({ remote_url:project.remote_url, username:'private-user', token:'private-token', clone_now:true });
  expect(screen.queryByLabelText('Access token')).not.toBeInTheDocument();
});

test('replacing only the token does not send a blank username', async () => {
  const fetch = api([project]); render(<CodeProjectsView />);
  fireEvent.click(await screen.findByRole('button',{name:'Edit Payments'}));
  fireEvent.change(screen.getByLabelText('Access token'), {target:{value:'new-private-token'}});
  fireEvent.click(screen.getByRole('button',{name:'Save project'}));
  await screen.findByText('Code project saved.');
  const body = JSON.parse(fetch.mock.calls.find(([,init]) => init?.method==='PUT')![1]!.body as string);
  expect(body.token).toBe('new-private-token');
  expect(body).not.toHaveProperty('username');
});


test('selects nested folders across branches and keeps selections when collapsed and reopened', async () => {
  const user = userEvent.setup(); const fetch = api(); render(<CodeProjectsView />);
  await user.click(screen.getByRole('button', {name:'Add project'}));
  await user.type(screen.getByLabelText('Project name'), 'Billing');
  await screen.findByRole('checkbox', {name:'Select folder api'});
  expect(fetch.mock.calls.some(([url]) => url.includes('path=api&'))).toBe(false);
  await user.click(screen.getByRole('button', {name:'Expand api'}));
  await user.click(await screen.findByRole('button', {name:'Expand api/src'}));
  await user.click(await screen.findByRole('checkbox', {name:'Select folder api/src/billing'}));
  await user.click(screen.getByRole('checkbox', {name:'Select folder shared'}));
  await user.click(screen.getByRole('button', {name:'Collapse api'}));
  expect(screen.queryByRole('checkbox', {name:'Select folder api/src/billing'})).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name:'Unmap api/src/billing'})).toBeVisible();
  await user.click(screen.getByRole('button', {name:'Expand api'}));
  expect(await screen.findByRole('checkbox', {name:'Select folder api/src/billing'})).toBeChecked();
  await user.click(screen.getByRole('button', {name:'Save project'}));
  await screen.findByText('Code project saved.');
  const body=JSON.parse(fetch.mock.calls.find(([,init]) => init?.method==='POST')![1]!.body as string);
  expect(body.folder_paths).toEqual(['api/src/billing','shared']);
});

test('editing expands selected folder ancestors and allows removing a nested mapping', async () => {
  const fetch = api([{...project, remote_url:null, clone_path:null, current_commit:null, checkout_path:null,
    folder_paths:['api/src/billing','shared'], has_token:false}]); render(<CodeProjectsView />);
  fireEvent.click(await screen.findByRole('button', {name:'Edit Payments'}));
  expect(await screen.findByRole('checkbox', {name:'Select folder api/src/billing'})).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', {name:'Select folder api/src/billing'}));
  fireEvent.click(screen.getByRole('button', {name:'Save project'}));
  await screen.findByText('Code project saved.');
  const body=JSON.parse(fetch.mock.calls.find(([,init]) => init?.method==='PUT')![1]!.body as string);
  expect(body.folder_paths).toEqual(['shared']);
  expect(body.revision).toBe(3);
});

test('code projects have no Searchable switch or collection field and never call RAG', async () => {
  const fetch = api([project]); render(<CodeProjectsView />);
  expect((await screen.findAllByText('Payments')).length).toBeGreaterThan(0);
  expect(screen.queryByRole('switch', { name: /Searchable/ })).toBeNull();
  expect(screen.queryByLabelText(/Search collection/)).toBeNull();
  expect(fetch.mock.calls.some(([url]) => String(url).startsWith('/api/rag/'))).toBe(false);
});

test('Remove asks for confirmation, keeps files by default and drops the project from the list', async () => {
  const user = userEvent.setup(); const fetch = api([project]); render(<CodeProjectsView />);
  await user.click(await screen.findByRole('button', { name: 'Remove Payments' }));
  const dialog = await screen.findByRole('alertdialog');
  expect(dialog).toHaveTextContent('code/payments');
  const box = screen.getByRole('checkbox', { name: /Also delete these folders and the code graph/ });
  expect(box).not.toBeChecked();
  expect(fetch.mock.calls.some(([url]) => String(url).includes('/archive'))).toBe(false);
  await user.click(screen.getByRole('button', { name: 'Remove project' }));
  await screen.findByText('Payments removed. Files on disk were kept.');
  const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/api/code-projects/code-1/archive'))!;
  expect(call[1]?.method).toBe('POST');
  expect(JSON.parse(call[1]!.body as string)).toEqual({ delete_files: false });
});

test('Remove with the delete box sends delete_files and reports what was deleted or left behind', async () => {
  const user = userEvent.setup(); const fetch = api([project]); const base = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (raw: string, init?: RequestInit) => {
    if (new URL(raw, 'http://localhost').pathname.endsWith('/archive')) return new Response(JSON.stringify({ id: 'code-1', deleted: ['code/payments'], errors: ['code-index/code-1 could not be deleted: Permission denied'] }), { status: 200 });
    return base(raw, init);
  });
  render(<CodeProjectsView />);
  await user.click(await screen.findByRole('button', { name: 'Remove Payments' }));
  await user.click(screen.getByRole('checkbox', { name: /Also delete these folders and the code graph/ }));
  await user.click(screen.getByRole('button', { name: 'Remove project' }));
  await screen.findByText('Payments removed. Deleted code/payments.');
  expect(screen.getByText(/code-index\/code-1 could not be deleted: Permission denied/)).toBeInTheDocument();
  const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/archive'))!;
  expect(JSON.parse(call[1]!.body as string)).toEqual({ delete_files: true });
});

test('a refused Remove shows the reason and keeps the project', async () => {
  const user = userEvent.setup(); const fetch = api([project]); const base = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (raw: string, init?: RequestInit) => {
    if (new URL(raw, 'http://localhost').pathname.endsWith('/archive')) return new Response(JSON.stringify({ error: { message: 'RAG collection code-docs indexes code/payments. Nothing was removed; remove that folder in RAG → Collections first or keep the files.' } }), { status: 409 });
    return base(raw, init);
  });
  render(<CodeProjectsView />);
  await user.click(await screen.findByRole('button', { name: 'Remove Payments' }));
  await user.click(screen.getByRole('checkbox', { name: /Also delete these folders and the code graph/ }));
  await user.click(screen.getByRole('button', { name: 'Remove project' }));
  await screen.findByText(/RAG collection code-docs indexes code\/payments/);
  expect(screen.getByRole('button', { name: 'Remove Payments' })).toBeInTheDocument();
});
