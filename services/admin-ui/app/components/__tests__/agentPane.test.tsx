import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import AgentPane from '../AgentPane';
import { defaultSettings } from '~/lib/agentSettings';
import { options } from '~/lib/__tests__/agentOptionsFixture';

const live = vi.hoisted(() => ({ sessions: {} as Record<string, any>, requested: [] as (string | null)[] }));
vi.mock('~/lib/useSessionEvents', () => ({
  useSessionEvents: (id: string | null) => {
    live.requested.push(id);
    const session = id ? live.sessions[id] ?? null : null;
    return { session, setSession: vi.fn(), error: id && !session ? 'Session not found' : '', setError: vi.fn(), events: [],
      older: 'start', loadOlder: vi.fn(), toolCounts: undefined, reconnecting: false };
  },
  useSessionNotice: () => ({ notice: null, clear: vi.fn(), stopping: vi.fn(), sent: vi.fn() }),
}));
vi.mock('../RunWorkspace', () => ({ default: ({ run, heading, headerActions, empty, composer, compact }: Record<string, any>) => <div data-compact={String(compact)}>
  {heading}{headerActions}{run.error && <p>{run.error}</p>}{empty}
  <label>Message<textarea value={composer.value} onChange={e => composer.onChange(e.target.value)} placeholder={composer.placeholder} /></label>
  {composer.blockedReason && <p>{composer.blockedReason}</p>}
  <button type="button" onClick={composer.onSend}>Send message</button></div> }));

const at = '2026-10-12T10:00:00Z';
const run = (id: string, status: string, title = `Run ${id}`) => ({ id, title, status, model_name: 'GPT', started_by: null, created_at: at, last_activity_at: at, draft_id: null });
const detail = (id: string, status: string) => ({ id, title: `Run ${id}`, status, purpose: 'revision', target_document_id: 'doc-1',
  agent_name: 'Ad-hoc agent', model_name: 'GPT', turns: 1, config: {}, document_draft: { id: `d-${id}`, document_id: 'doc-1', status: 'open' } });

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
const RUNS = 'GET /api/agent-sessions?document_id=doc-1';
const settingsOf = () => ({ options, error: '', settings: defaultSettings(options), update: vi.fn(), replace: vi.fn() });
function show(props: Partial<Parameters<typeof AgentPane>[0]> = {}) {
  const onDraftChange = vi.fn();
  render(<MemoryRouter><AgentPane documentId="doc-1" documentTitle="Runbook" settings={settingsOf()} initialRunId={null}
    confirmUnsaved={run => run()} onDraftChange={onDraftChange} {...props} /></MemoryRouter>);
  return { onDraftChange };
}
afterEach(() => { vi.unstubAllGlobals(); live.sessions = {}; live.requested = []; });

test('no open run: an empty conversation with the settings summary; the first message starts a run', async () => {
  const calls = api({ [RUNS]: () => [200, { sessions: [run('r0', 'closed')] }],
    'POST /api/agent-sessions': () => { live.sessions.r1 = detail('r1', 'starting'); return [201, detail('r1', 'starting')]; } });
  show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  expect(within(pane).getByText('Ask the agent to change this document.')).toBeInTheDocument();
  expect(within(pane).getByLabelText('Current settings')).toHaveTextContent('Claude · web-1, Payments · 2 tools');
  expect(within(pane).getByRole('button', { name: 'New chat' })).toBeDisabled();
  fireEvent.change(within(pane).getByLabelText('Message'), { target: { value: 'Add log rotation.' } });
  fireEvent.click(within(pane).getByRole('button', { name: 'Send message' }));
  await waitFor(() => expect(calls.find(c => c.key === 'POST /api/agent-sessions')?.body).toEqual({
    kind: 'revision', target: { document_id: 'doc-1' }, instruction: 'Add log rotation.', model_id: 'm2', prompt_id: 'p1',
    system_prompt: 'You revise carefully.', resources: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: [], collection_ids: [] },
    tools: ['ssh_run', 'get_service_info'], expert_ids: [], subagents: false }));
  await waitFor(() => expect(live.requested.at(-1)).toBe('r1'));
  expect(within(pane).getByLabelText('Message')).toHaveValue('');
});

test('the latest open run opens; messages go to it; its draft is reported', async () => {
  live.sessions.r2 = detail('r2', 'waiting');
  const calls = api({ [RUNS]: () => [200, { sessions: [run('r2', 'waiting'), run('r1', 'closed')] }],
    'POST /api/generations/sessions/r2/messages': () => [202, { working: true }] });
  const { onDraftChange } = show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  await waitFor(() => expect(live.requested.at(-1)).toBe('r2'));
  expect(onDraftChange).toHaveBeenLastCalledWith({ id: 'd-r2', document_id: 'doc-1', status: 'open' });
  fireEvent.change(within(pane).getByLabelText('Message'), { target: { value: 'Also mention cron.' } });
  fireEvent.click(within(pane).getByRole('button', { name: 'Send message' }));
  await waitFor(() => expect(calls.find(c => c.key === 'POST /api/generations/sessions/r2/messages')?.body).toEqual({ text: 'Also mention cron.' }));
  expect(calls.some(c => c.key === 'POST /api/agent-sessions')).toBe(false);
});

test('New chat closes the open run; while the agent works it asks first', async () => {
  live.sessions.r2 = detail('r2', 'working');
  const calls = api({ [RUNS]: () => [200, { sessions: [run('r2', 'working')] }],
    'POST /api/generations/sessions/r2/close': () => [200, { ...detail('r2', 'closed') }] });
  show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  await waitFor(() => expect(within(pane).getByRole('button', { name: 'New chat' })).toBeEnabled());
  fireEvent.click(within(pane).getByRole('button', { name: 'New chat' }));
  const dialog = await screen.findByRole('alertdialog', { name: 'Close this conversation?' });
  expect(dialog).toHaveTextContent('The agent is still working. Closing stops it.');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Keep it' }));
  expect(calls.some(c => c.key === 'POST /api/generations/sessions/r2/close')).toBe(false);
  fireEvent.click(within(pane).getByRole('button', { name: 'New chat' }));
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Close and start new' }));
  await waitFor(() => expect(calls.some(c => c.key === 'POST /api/generations/sessions/r2/close')).toBe(true));
  await waitFor(() => expect(live.requested.at(-1)).toBeNull());
  expect(within(pane).getByText('Ask the agent to change this document.')).toBeInTheDocument();
});

test('a refused start keeps the message and marks Settings', async () => {
  api({ [RUNS]: () => [200, { sessions: [] }],
    'POST /api/agent-sessions': () => [400, { error: { message: 'ssh_run needs at least one server.' } }] });
  show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  fireEvent.change(within(pane).getByLabelText('Message'), { target: { value: 'Add log rotation.' } });
  fireEvent.click(within(pane).getByRole('button', { name: 'Send message' }));
  expect(await within(pane).findByText('ssh_run needs at least one server.')).toBeInTheDocument();
  expect(within(pane).getByLabelText('Message')).toHaveValue('Add log rotation.');
  expect(within(pane).getByRole('button', { name: 'Settings (check the settings)' })).toBeInTheDocument();
});

test('a closed run blocks the message box and points to New chat', async () => {
  live.sessions.r3 = detail('r3', 'closed');
  api({ [RUNS]: () => [200, { sessions: [run('r3', 'closed')] }] });
  show({ initialRunId: 'r3' });
  expect(await screen.findByText('This conversation is closed. Start a New chat.')).toBeInTheDocument();
});

test('Earlier runs opens another run', async () => {
  live.sessions.r2 = detail('r2', 'waiting'); live.sessions.r1 = detail('r1', 'closed');
  api({ [RUNS]: () => [200, { sessions: [run('r2', 'waiting'), run('r1', 'closed', 'Remove the old steps.')] }] });
  show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  await waitFor(() => expect(live.requested.at(-1)).toBe('r2'));
  fireEvent.keyDown(within(pane).getByRole('button', { name: 'Earlier runs' }), { key: 'Enter' });
  fireEvent.click(await screen.findByRole('menuitem', { name: /Remove the old steps\./ }));
  await waitFor(() => expect(live.requested.at(-1)).toBe('r1'));
});

test('an unknown run shows the error and New chat clears it', async () => {
  api({ [RUNS]: () => [200, { sessions: [] }] });
  show({ initialRunId: 'nope' });
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  expect(await within(pane).findByText('Session not found')).toBeInTheDocument();
  fireEvent.click(within(pane).getByRole('button', { name: 'New chat' }));
  await waitFor(() => expect(live.requested.at(-1)).toBeNull());
  expect(within(pane).getByText('Ask the agent to change this document.')).toBeInTheDocument();
});

test('the conversation uses the compact workspace', async () => {
  api({ [RUNS]: () => [200, { sessions: [] }] });
  show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  expect(pane.querySelector('[data-compact="true"]')).not.toBeNull();
});

test('while a run is open, Settings shows the run\'s saved configuration, read-only', async () => {
  live.sessions.r2 = { ...detail('r2', 'waiting'), model_id: 'm1', run_resources: { server_ids: ['s2'], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] } };
  api({ [RUNS]: () => [200, { sessions: [run('r2', 'waiting')] }] });
  const props = settingsOf();
  show({ settings: props });
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  await waitFor(() => expect(live.requested.at(-1)).toBe('r2'));
  fireEvent.click(within(pane).getByRole('button', { name: 'Settings' }));
  const sheet = await screen.findByRole('dialog');
  // the page's defaults check web-1 and Payments; the run's configuration has prod-1 only
  expect(within(sheet).getByRole('checkbox', { name: 'prod-1' })).toBeChecked();
  expect(within(sheet).getByRole('checkbox', { name: 'web-1' })).not.toBeChecked();
  expect(within(sheet).getByRole('checkbox', { name: 'Payments' })).not.toBeChecked();
  expect(within(sheet).getByRole('checkbox', { name: 'web-1' })).toBeDisabled();
});
