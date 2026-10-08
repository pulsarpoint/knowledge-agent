import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AgentTestView from '../AgentTestView';
import * as tests from '../../lib/testSessions';
import * as reviews from '../../lib/reviews';
import type { SessionDetail } from '../../lib/claudeCode';
import { getAgentConfig, getRunResourceOptions, type RunResourceOptions } from '../../lib/profiles';
vi.mock('../../lib/testSessions');
vi.mock('../../lib/reviews');
vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgentConfig: vi.fn(), getRunResourceOptions: vi.fn() }));
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

class Stream extends EventTarget {
  static instances: Stream[] = [];
  close = vi.fn();
  constructor(public url: string) { super(); Stream.instances.push(this); }
}
const session = {
  id: 's1', title: 'How many retries?', purpose: 'test', runtime: 'openai', agent_id: 'custom', agent_name: 'Custom agent',
  model_name: 'GPT', model_id: 'gpt', status: 'waiting', status_message: '', turns: 1, input_tokens: 120, output_tokens: 30,
  cached_tokens: 0, cost_usd: 0, model_calls: 1, reasoning_tokens: 0, draft: null, draft_turn: null, document_id: null,
  document_title: null, document_name: null, folder: null, worker_active: true, queued: 0, run_resources: null,
  prompt_snapshot: { system_prompt: 'Help.' }, context_snapshot: { folders: [], code_project_ids: [] }, inputs: [], tool_counts: [],
  tool_counts_through_id: 0, config: { id: 'custom', system_prompt: { content: 'Pinned prompt.' } }, reviews: [],
  created_at: '2026-10-08T10:00:00Z', last_activity_at: '2026-10-08T10:00:00Z', closed_at: null,
} as unknown as SessionDetail;
const turn1 = [
  { id: 1, turn: 1, seq: 1, kind: 'input', payload: { task: 'How many retries?' } },
  { id: 2, turn: 1, seq: 2, kind: 'message', payload: { text: 'Three retries.' } },
  { id: 3, turn: 1, seq: 3, kind: 'end', payload: { status: 'completed' } }];
const posts: { url: string; body?: unknown }[] = [];
let detail: SessionDetail = structuredClone(session);
function Page(props: { available?: boolean; dirty?: boolean }) {
  const location = useLocation();
  return <><output aria-label="URL">{location.search}</output>
    <AgentTestView agentId="custom" agentName="Custom agent" available={props.available ?? true} ready dirty={props.dirty ?? false} active /></>;
}
const renderPage = (query = '?view=test', props: { available?: boolean; dirty?: boolean } = {}) =>
  render(<MemoryRouter initialEntries={[query]}><Page {...props} /></MemoryRouter>);
beforeEach(() => {
  vi.resetAllMocks();
  Stream.instances = []; posts.length = 0; detail = structuredClone(session);
  vi.stubGlobal('EventSource', Stream);
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      posts.push({ url, body: init.body ? JSON.parse(init.body as string) : undefined });
      if (url.endsWith('/messages')) return Response.json({ seq: 2, status: 'queued', working: false }, { status: 202 });
      if (url.endsWith('/stop')) return Response.json({ id: 's1', discarded: 0 }, { status: 202 });
      return Response.json({ id: 's1' });
    }
    if (url.includes('/events/history')) return Response.json({ events: turn1, has_more: false });
    return Response.json(detail);
  }));
  vi.mocked(getAgentConfig).mockResolvedValue({ id: 'custom', system_prompt: { content: 'Current prompt.' } });
  vi.mocked(getRunResourceOptions).mockResolvedValue({ resources: 'fixed' });
  vi.mocked(tests.getTestSessions).mockResolvedValue({ sessions: [] });
});
afterEach(() => vi.unstubAllGlobals());

it('the first message starts a Test session and opens it; a follow-up goes to the same session', async () => {
  vi.mocked(tests.createTestSession).mockResolvedValue(structuredClone(session));
  renderPage();
  expect(screen.getByText('Chat with Custom agent')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'How many retries?' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Run task' })).toBeEnabled());  // after the run resource options load
  fireEvent.click(screen.getByRole('button', { name: 'Run task' }));
  await waitFor(() => expect(screen.getByLabelText('URL')).toHaveTextContent('session_id=s1'));
  expect(tests.createTestSession).toHaveBeenCalledWith('custom', 'How many retries?', undefined);
  expect(await screen.findByText('Three retries.')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Full record' })).toHaveAttribute('href', '/activity/runs/cc%3As1%3A1');
  expect(screen.getByLabelText('Message')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'And timeouts?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  await waitFor(() => expect(posts).toContainEqual({ url: '/api/claude-code/sessions/s1/messages', body: { text: 'And timeouts?' } }));
  expect(await screen.findByText('Sent. The agent starts a new turn with your message.')).toBeVisible();
  expect(tests.createTestSession).toHaveBeenCalledTimes(1);
});

it('History lists this agent\'s Test sessions and opens one; New session starts empty', async () => {
  vi.mocked(tests.getTestSessions).mockResolvedValue({ sessions: [{ id: 's1', title: 'How many retries?', status: 'waiting',
    created_at: '2026-10-08T10:00:00Z', last_activity_at: '2026-10-08T10:00:00Z', actor_email: 'admin@test' }] });
  renderPage();
  fireEvent.click(screen.getByRole('button', { name: 'History' }));
  const sheet = within(await screen.findByRole('dialog'));
  expect(await sheet.findByText('How many retries?')).toBeVisible();
  expect(sheet.getByText(/Waiting for you · admin@test/)).toBeVisible();
  expect(tests.getTestSessions).toHaveBeenCalledWith('custom');
  fireEvent.click(sheet.getByRole('button', { name: 'Open session' }));
  await waitFor(() => expect(screen.getByLabelText('URL')).toHaveTextContent('session_id=s1'));
  expect(await screen.findByText('Three retries.')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'New session' }));
  await waitFor(() => expect(screen.getByLabelText('URL')).not.toHaveTextContent('session_id'));
  expect(screen.queryByText('Three retries.')).toBeNull();
});

it('Show config shows the current configuration before the first message and the pinned one in a session', async () => {
  const view = renderPage();
  fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
  expect(await screen.findByText('Current agent configuration')).toBeVisible();
  expect(JSON.parse((await screen.findByLabelText('Agent configuration')).textContent!).config.system_prompt.content).toBe('Current prompt.');
  view.unmount();
  renderPage('?view=test&session_id=s1');
  await screen.findByText('Three retries.');
  fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
  expect(await screen.findByText('Saved session configuration')).toBeVisible();
  expect(JSON.parse(screen.getByLabelText('Agent configuration').textContent!).config.system_prompt.content).toBe('Pinned prompt.');
  expect(getAgentConfig).toHaveBeenCalledTimes(1);
});

it('a per-run agent chooses its resources from the Resources line before the first message only', async () => {
  const chosen = { code_project_ids: ['app'], collection_ids: [], server_ids: [], service_ids: [] };
  vi.mocked(getRunResourceOptions).mockResolvedValue({ resources: 'per_run', code_projects: [{ id: 'app', name: 'Payments API' },
    { id: 'worker', name: 'Worker' }] } as RunResourceOptions);
  detail = { ...structuredClone(session), run_resources: chosen,
    config: { id: 'custom', run_resources: chosen, code: { projects: [{ id: 'app', name: 'Payments API' }] } } };
  vi.mocked(tests.createTestSession).mockResolvedValue(structuredClone(detail));
  renderPage();
  expect(await screen.findByLabelText('Resources for this run')).toHaveTextContent('Resources: nothing');
  fireEvent.click(screen.getByRole('button', { name: 'Choose resources' }));
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(screen.getByLabelText('Resources for this run')).toHaveTextContent('Resources: Payments API');
  expect(screen.getByText('Resources: Per run: Payments API')).toBeVisible();  // the header
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Where are the limits?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run task' }));
  await waitFor(() => expect(tests.createTestSession).toHaveBeenCalledWith('custom', 'Where are the limits?', chosen));
  expect(await screen.findByText('Three retries.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Choose resources' })).toBeNull();  // fixed for the session
  expect(screen.getByText('Resources: Per run: Payments API')).toBeVisible();  // from the pinned configuration
});

it('feedback on an answer is reviewed, listed under Suggestions and opens Review and apply', async () => {
  const review = { id: 'review-1', comment: 'Cite sources', suggestion: 'Cite the runbook.', response_turn: 1, created_at: '2026-10-08T10:00:00Z' };
  vi.mocked(tests.reviewTestSession).mockRejectedValueOnce(new Error('Review unavailable')).mockResolvedValueOnce(review);
  vi.mocked(reviews.getReviewSource).mockReturnValue(new Promise(() => {}));
  renderPage('?view=test&session_id=s1');
  await screen.findByText('Three retries.');
  expect(screen.getByRole('button', { name: 'Suggestions (0)' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Add feedback' }));
  fireEvent.change(screen.getByLabelText('Your feedback'), { target: { value: 'Cite sources' } });
  fireEvent.click(screen.getByRole('button', { name: 'Suggest improvements' }));
  expect(await screen.findByText('Review unavailable')).toBeVisible();
  expect(screen.getByLabelText('Your feedback')).toHaveValue('Cite sources');  // kept for another try
  fireEvent.click(screen.getByRole('button', { name: 'Suggest improvements' }));
  expect(await screen.findByText('Cite the runbook.')).toBeVisible();
  expect(tests.reviewTestSession).toHaveBeenLastCalledWith('custom', 's1', 'Cite sources', 1);
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Suggestions (1)' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Review and apply' }));
  expect(await screen.findByText('Apply suggestions')).toBeVisible();
  expect(reviews.getReviewSource).toHaveBeenCalledWith('custom', 'review-1');
});

it('blocked reasons: a disabled agent, unsaved changes and a closed session; Close session asks first', async () => {
  const disabled = renderPage('?view=test', { available: false });
  expect(screen.getByText('This agent is disabled. Enable it in General to run a task.')).toBeVisible();
  disabled.unmount();
  const dirty = renderPage('?view=test', { dirty: true });
  expect(screen.getByText('Save or discard changes in the configuration tabs before starting a task.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Show config' })).toBeDisabled();
  dirty.unmount();
  renderPage('?view=test&session_id=s1');
  await screen.findByText('Three retries.');
  fireEvent.click(screen.getByRole('button', { name: 'Close session' }));
  expect(posts).toEqual([]);
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Close session' }));
  await waitFor(() => expect(posts.map(p => p.url)).toEqual(['/api/claude-code/sessions/s1/close']));
  expect(await screen.findByText('This session is closed. Start a new session to continue.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Close session' })).toBeNull();
});

it('Stop asks the working agent to stop', async () => {
  detail = { ...structuredClone(session), status: 'working' };
  renderPage('?view=test&session_id=s1');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await waitFor(() => expect(posts.map(p => p.url)).toContain('/api/claude-code/sessions/s1/stop'));
  expect(await screen.findByText('Stopping the current turn…')).toBeVisible();
});

it('a session of another agent is not opened here', async () => {
  detail = { ...structuredClone(session), agent_id: 'other' };
  renderPage('?view=test&session_id=s1');
  expect(await screen.findByText('Session not found for this agent.')).toBeVisible();
  expect(screen.queryByText('Three retries.')).toBeNull();
});

const ANALYST: RunResourceOptions = { resources: 'per_run', collections: null, servers: [], services: null,
  code_projects: [{ id: 'app', name: 'Payments API' }, { id: 'worker', name: 'Worker' }] } as RunResourceOptions;
const ONLY_APP = { code_project_ids: ['app'], collection_ids: [], server_ids: [], service_ids: [] };
const chooseResources = async () => {
  fireEvent.click(await screen.findByRole('button', { name: 'Choose resources' }));
  return within(await screen.findByRole('dialog'));
};

it('the first message waits for the resource options: a load failure blocks it until Retry', async () => {
  let resolve!: (value: RunResourceOptions) => void;
  vi.mocked(getRunResourceOptions).mockReturnValueOnce(new Promise(done => { resolve = done; }))
    .mockRejectedValueOnce(new Error('Options unavailable')).mockResolvedValue(structuredClone(ANALYST));
  const view = (revision: number) => <MemoryRouter initialEntries={['?view=test']}><AgentTestView agentId="custom" agentName="Custom agent" available ready dirty={false} active revision={revision} /></MemoryRouter>;
  const { rerender } = render(view(1));
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Too early' } });
  expect(screen.getByText('Loading the resources for this run…')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Run task' })).toBeDisabled();
  await act(async () => resolve(structuredClone(ANALYST)));
  expect(screen.getByRole('button', { name: 'Run task' })).toBeEnabled();
  rerender(view(2));
  expect(await screen.findByRole('button', { name: 'Retry' })).toBeVisible();
  expect(screen.getAllByText('Options unavailable').length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: 'Run task' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Run task' })).toBeEnabled());
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
});

it('a choice the reloaded options no longer offer is not sent; the old options stay while reloading', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  vi.mocked(tests.createTestSession).mockResolvedValue(structuredClone(session));
  const view = (revision: number) => <MemoryRouter initialEntries={['?view=test']}><AgentTestView agentId="custom" agentName="Custom agent" available ready dirty={false} active revision={revision} /></MemoryRouter>;
  const { rerender } = render(view(1));
  const picker = await chooseResources();
  fireEvent.click(picker.getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.click(picker.getByRole('checkbox', { name: 'Worker' }));
  let resolve!: (value: RunResourceOptions) => void;
  vi.mocked(getRunResourceOptions).mockReturnValue(new Promise(done => { resolve = done; }));
  rerender(view(2));
  expect(screen.getByRole('checkbox', { name: 'Worker' })).toBeVisible();
  await act(async () => resolve({ ...structuredClone(ANALYST), code_projects: [{ id: 'app', name: 'Payments API' }] }));
  await waitFor(() => expect(screen.queryByRole('checkbox', { name: 'Worker' })).toBeNull());
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Where are the limits?' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run task' }));
  await waitFor(() => expect(tests.createTestSession).toHaveBeenCalledWith('custom', 'Where are the limits?', ONLY_APP));
});

it('the resource picker hides unused kinds, explains empty ones and says when nothing is chosen', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  renderPage();
  const picker = await chooseResources();
  expect(picker.queryByText('Collections')).toBeNull();  // a kind the agent does not use
  expect(picker.getByText('None allowed. Choose them in the agent’s tabs.')).toBeVisible();  // servers
  const note = 'Nothing chosen: the agent’s tools will have nothing to work on';
  expect(picker.getByText(note)).toBeVisible();
  fireEvent.click(picker.getByRole('checkbox', { name: 'Payments API' }));
  expect(picker.queryByText(note)).toBeNull();
});

it('Show config lists the chosen run resources before the first message and the tool counts in a session', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  const view = renderPage();
  fireEvent.click((await chooseResources()).getByRole('checkbox', { name: 'Payments API' }));
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
  expect(JSON.parse((await screen.findByLabelText('Agent configuration')).textContent!).run_resources).toEqual(ONLY_APP);
  view.unmount();
  detail = { ...structuredClone(session), tool_counts: [{ name: 'read_file', calls: 2 }], tool_counts_through_id: 3 };
  renderPage('?view=test&session_id=s1');
  await screen.findByText('Three retries.');
  fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
  expect(JSON.parse((await screen.findByLabelText('Agent configuration')).textContent!).tool_counts).toEqual([{ name: 'read_file', calls: 2 }]);
});

it('the header shows the resources a session started with, whatever the agent uses now', async () => {
  vi.mocked(getRunResourceOptions).mockResolvedValue(structuredClone(ANALYST));
  const fixed = renderPage('?view=test&session_id=s1');  // a fixed session; the agent is per-run now
  await screen.findByText('Three retries.');
  expect(screen.getByText('Resources: Fixed')).toBeVisible();
  fixed.unmount();
  vi.mocked(getRunResourceOptions).mockResolvedValue({ resources: 'fixed' });
  detail = { ...structuredClone(session), config: { id: 'custom', run_resources: ONLY_APP, code: { projects: [{ id: 'app', name: 'Payments API' }] } } };
  renderPage('?view=test&session_id=s1');
  expect(await screen.findByText('Resources: Per run: Payments API')).toBeVisible();
});

it('feedback is cleared after a review, and an answer takes no feedback while the agent works', async () => {
  vi.mocked(tests.reviewTestSession).mockResolvedValue({ id: 'review-1', comment: 'Cite sources', suggestion: 'Cite the runbook.', response_turn: 1, created_at: '2026-10-08T10:00:00Z' });
  const idle = renderPage('?view=test&session_id=s1');
  await screen.findByText('Three retries.');
  fireEvent.click(screen.getByRole('button', { name: 'Add feedback' }));
  fireEvent.change(screen.getByLabelText('Your feedback'), { target: { value: 'Cite sources' } });
  fireEvent.click(screen.getByRole('button', { name: 'Suggest improvements' }));
  expect(await screen.findByText('Cite the runbook.')).toBeVisible();
  expect(screen.getByLabelText('Your feedback')).toHaveValue('');
  idle.unmount();
  detail = { ...structuredClone(session), status: 'working' };
  renderPage('?view=test&session_id=s1');
  await screen.findByText('Three retries.');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add feedback' })).toBeDisabled());
});

it('a session that cannot be opened says so and blocks the message box', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ error: { message: 'Session not found.' } }, { status: 404 })));
  renderPage('?view=test&session_id=missing');
  expect(await screen.findByText('Could not open this session. Start a new session.')).toBeVisible();
  expect(screen.queryByText('Loading conversation…')).toBeNull();
});

it('a History load failure shows in the History panel', async () => {
  vi.mocked(tests.getTestSessions).mockRejectedValue(new Error('Sessions unavailable'));
  renderPage();
  fireEvent.click(screen.getByRole('button', { name: 'History' }));
  const sheet = within(await screen.findByRole('dialog'));
  expect(await sheet.findByText('Sessions unavailable')).toBeVisible();
  expect(sheet.queryByText('Loading sessions…')).toBeNull();
});

it('the Test tab fills the screen below the agent page header, so the message box stays visible', async () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => ({ top: 300 } as DOMRect));
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 900 });
  try {
    renderPage();
    const tab = screen.getByRole('form', { name: 'Message box' }).closest<HTMLElement>('[style]');
    expect(tab?.style.height).toBe('576px');  // 900 - 300 - 24 (the main padding)
  } finally { vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockRestore(); }
});
