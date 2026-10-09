import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { expect, test, vi } from 'vitest';
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div> }));
import RunWorkspace, { type RunWorkspaceProps } from '../RunWorkspace';
import type { SessionDetail, SessionEvent } from '~/lib/generations';
import type { SessionRun } from '~/lib/useSessionEvents';

const detail: SessionDetail = {
  id: 's1', title: 'How many retries?', agent_id: 'custom', agent_name: 'Payments helper', model_name: 'GPT',
  status: 'waiting', status_message: '', turns: 2, input_tokens: 1300, output_tokens: 300, cached_tokens: 20,
  purpose: 'test', model_calls: 2, reasoning_tokens: 30, document_id: null, document_title: null, folder: null,
  worker_active: true, queued: 0, created_at: '2026-10-08T10:00:00Z', last_activity_at: '2026-10-08T10:00:00Z', closed_at: null,
  document_name: null, draft: null, draft_turn: null, model_id: 'gpt', prompt_snapshot: { system_prompt: 'Help.' },
  context_snapshot: { folders: [], code_project_ids: [] }, inputs: [], tool_counts: [{ name: 'search_documents', calls: 1 }],
};
let next = 0;
const event = (turn: number, kind: string, payload: Record<string, unknown> = {}): SessionEvent =>
  ({ id: ++next, turn, seq: next, kind, payload, created_at: '2026-10-08T10:00:00Z' });
const conversation = [
  event(1, 'input', { task: 'How many retries?' }),
  event(1, 'tool_call', { name: 'search_documents', call_id: 'c1', summary: 'retries' }),
  event(1, 'usage', { input_tokens: 1300, output_tokens: 200, total_tokens: 1500 }),
  event(1, 'tool_result', { name: 'search_documents', call_id: 'c1', preview: '[1] Three retries.' }),
  event(1, 'message', { text: 'Three retries [1].' }),
  event(1, 'end', { status: 'completed', grounding: { sources: [{ n: 1, path: 'payments/retries.md', heading_path: 'payments', anchor: null }], not_covered: false } }),
  event(2, 'input', { task: 'And timeouts?' }), event(2, 'error', { message: 'Agent execution failed (RuntimeError).' }), event(2, 'end', { status: 'failed' }),
  event(3, 'input', { task: 'Wait.' }), event(3, 'end', { status: 'stopped' }),
];
const run = (change: Partial<SessionRun> = {}): SessionRun => ({ session: detail, setSession: vi.fn(), events: conversation, older: 'start',
  loadOlder: vi.fn(async () => {}), toolCounts: detail.tool_counts, reconnecting: false, error: '', setError: vi.fn(), ...change });
const composer = { value: '', onChange: vi.fn(), onSend: vi.fn() };
function show(props: Partial<RunWorkspaceProps> = {}) {
  const all: RunWorkspaceProps = { run: run(), agentName: 'Payments helper', resources: 'Per run: App', composer, ...props };
  return render(<MemoryRouter><RunWorkspace {...all} /></MemoryRouter>);
}

test('the header shows the agent, its resources, the status, the tokens and the tools used', () => {
  show({ headerActions: <button type="button">History</button> });
  expect(screen.getByText('Resources: Per run: App')).toBeVisible();
  expect(screen.getByText('Waiting for you')).toBeVisible();
  expect(screen.getByLabelText('Token totals')).toHaveTextContent('In 1,300 · Out 300 · Cached 20 · Model calls 2 · Reasoning 30');
  expect(screen.getByLabelText('Tools used')).toHaveTextContent('search_documents 1');
  expect(screen.getByRole('button', { name: 'History' })).toBeVisible();
});

test('a turn is your message, a collapsed Worked block and the answer with its sources; failed and stopped turns say so', () => {
  show({ answerActions: turn => <button type="button">Feedback on turn {turn.turn}</button> });
  const [first, second, third] = screen.getAllByRole('listitem').filter(item => item.hasAttribute('data-turn'));
  expect(within(first).getByLabelText('Your message')).toHaveTextContent('How many retries?');
  const worked = within(first).getByText('Worked · 1 tool call · 1.5k tokens');
  expect(within(first).getByText('retries')).not.toBeVisible();  // collapsed
  fireEvent.click(worked);
  expect(within(first).getByText('retries')).toBeVisible();
  expect(within(first).getByText('[1] Three retries.')).toBeVisible();
  expect(within(within(first).getByLabelText('Agent answer')).getByTestId('markdown')).toHaveTextContent('Three retries [1].');
  expect(within(first).getByLabelText('Cited sources')).toHaveTextContent('payments/retries.md');
  expect(within(first).getByRole('button', { name: 'Feedback on turn 1' })).toBeVisible();
  expect(within(second).getByText('Agent execution failed (RuntimeError).')).toBeVisible();
  expect(within(third).getByText('Stopped.')).toBeVisible();
});

test('a turn lists its model calls', () => {
  show();
  expect(screen.getByLabelText('Turn 1 usage')).toHaveTextContent('Model calls 1');
  expect(screen.getByLabelText('Turn 1 usage')).toHaveTextContent('In 1,300 · Out 200');
});

test('the main agent in the sidebar follows the session live', () => {
  const view = show();
  const agents = within(screen.getByRole('region', { name: 'Agents in this run' }));
  expect(agents.getByText('Payments helper')).toBeVisible();
  expect(agents.getByText('Waiting for you · 1 tool call · 1.6k tokens')).toBeVisible();
  view.rerender(<MemoryRouter><RunWorkspace run={run({ session: { ...detail, status: 'working', input_tokens: 4000, output_tokens: 600 },
    toolCounts: [{ name: 'search_documents', calls: 3 }] })} agentName="Payments helper" resources="Fixed" composer={composer} /></MemoryRouter>);
  expect(agents.getByText('Working · 3 tool calls · 4.6k tokens')).toBeVisible();
});

test('the message box stays usable while the agent works, next to Stop', () => {
  const send = vi.fn(), stop = vi.fn();
  show({ run: run({ session: { ...detail, status: 'working' }, events: [...conversation, event(4, 'input', { task: 'Also timeouts' })] }), composer: { value: 'Also cover timeouts', onChange: vi.fn(), onSend: send, onStop: stop } });
  const box = within(screen.getByRole('form', { name: 'Message box' }));
  expect(box.getByLabelText('Message')).toBeEnabled();
  fireEvent.click(box.getByRole('button', { name: 'Send message' }));
  fireEvent.click(box.getByRole('button', { name: 'Stop' }));
  expect([send, stop].map(fn => fn.mock.calls.length)).toEqual([1, 1]);
  expect(screen.getByRole('status')).toHaveTextContent('Working…');
});

test('a blocked message box says why; without events the empty state shows', () => {
  show({ run: run({ session: null, events: [], toolCounts: undefined }), empty: <p>Chat with Payments helper</p>,
    composer: { value: 'Hi', onChange: vi.fn(), onSend: vi.fn(), blockedReason: 'This agent is disabled. Enable it in General to run a task.' } });
  expect(screen.getByText('Chat with Payments helper')).toBeVisible();
  expect(screen.getByText('This agent is disabled. Enable it in General to run a task.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
  expect(screen.queryByLabelText('Token totals')).toBeNull();
});

test('the worker, the stream and errors are reported in the header', () => {
  show({ run: run({ session: { ...detail, status: 'starting', worker_active: false }, reconnecting: true, error: 'Could not load earlier activity' }) });
  expect(screen.getByText('Waiting for the agent session worker. The session starts when the worker is running.')).toBeVisible();
  expect(screen.getByText('Reconnecting…')).toBeVisible();
  expect(screen.getByText('Could not load earlier activity')).toBeVisible();
});

test('on narrow screens the sidebar opens as a drawer from the header', () => {
  show({ sidebar: <p>Draft · turn 3</p> });
  fireEvent.click(screen.getByRole('button', { name: 'Run details' }));
  const drawer = within(screen.getByRole('dialog'));
  expect(drawer.getByRole('region', { name: 'Agents in this run' })).toBeVisible();
  expect(drawer.getByText('Draft · turn 3')).toBeVisible();
});

test('the header row wraps on a phone instead of forcing a horizontal scroll', () => {
  show({ headerActions: <button type="button">History</button> });
  const row = screen.getByTestId('run-header');
  expect(row).toHaveClass('min-w-0', 'flex-wrap');
  for (const part of row.children) expect(part).toHaveClass('min-w-0', 'flex-wrap');
});

test('a failed turn shows its error once: the header repeats the session message only when no turn carries it', () => {
  const failed = { ...detail, status: 'failed' as const, status_message: 'Agent execution failed (RuntimeError).' };
  const lastFailed = conversation.slice(0, 9);  // turn 2, the last, failed with that error
  const view = show({ run: run({ session: failed, events: lastFailed }) });
  expect(screen.getAllByText('Agent execution failed (RuntimeError).')).toHaveLength(1);
  view.unmount();
  show({ run: run({ session: failed, events: [] }) });
  expect(screen.getByText('Agent execution failed (RuntimeError).')).toBeVisible();
});

test('the run pane keeps a minimum height so a tiny screen scrolls the page instead of collapsing the conversation', () => {
  show();
  expect(screen.getByRole('region', { name: 'Run' }).parentElement!.className).toContain('min-h-48');
});

const delegated = [
  event(5, 'input', { task: 'Compare the services.' }),
  event(5, 'tool_call', { name: 'run_subagent', call_id: 's1', summary: 'Read the worker', arguments: { task: 'Read the worker' } }),
  event(5, 'input', { task: 'Read the worker', subagent: { task: 'Read the worker' }, parent_call_id: 's1' }),
  event(5, 'tool_call', { name: 'read_file', call_id: 'r1', summary: 'w.py', parent_call_id: 's1' }),
  event(5, 'usage', { input_tokens: 20000, output_tokens: 3000, total_tokens: 23000, parent_call_id: 's1' }),
  event(5, 'tool_result', { name: 'read_file', call_id: 'r1', preview: 'def main', is_error: false, parent_call_id: 's1' }),
  event(5, 'message', { text: 'Worker reads w.py.', parent_call_id: 's1' }),
];

test('a subagent is a line in the Worked block and an entry in the sidebar; Open shows its own history', () => {
  const working = { ...detail, status: 'working' as const };
  const view = show({ run: run({ session: working, events: delegated }) });
  expect(screen.getByText('Subagent · Read the worker · 1 tool call · 23k tokens · working')).toBeInTheDocument();
  const agents = within(screen.getByRole('region', { name: 'Agents in this run' }));
  const entry = agents.getByRole('button', { name: /Read the worker/ });
  expect(entry).toHaveTextContent('Working · 1 tool call · 23k tokens');
  fireEvent.click(screen.getByRole('button', { name: 'Open subagent: Read the worker' }));
  const crumbs = screen.getByRole('navigation', { name: 'breadcrumb' });
  expect(crumbs).toHaveTextContent(/Main agent\s*›\s*Subagent: Read the worker/);
  expect(screen.getByLabelText('Subagent task')).toHaveTextContent('Read the worker');
  expect(within(screen.getByLabelText('Subagent answer')).getByTestId('markdown')).toHaveTextContent('Worker reads w.py.');
  expect(screen.getByText('def main')).toBeInTheDocument();  // its own Worked block
  expect(screen.queryByLabelText('Your message')).toBeNull();
  expect(entry).toHaveAttribute('aria-pressed', 'true');
  view.rerender(<MemoryRouter><RunWorkspace run={run({ session: working, events: [...delegated,
    event(5, 'tool_result', { name: 'run_subagent', call_id: 's1', preview: 'Worker reads w.py.', is_error: false })] })}
    agentName="Payments helper" resources="Fixed" composer={composer} /></MemoryRouter>);
  expect(agents.getByRole('button', { name: /Read the worker/ })).toHaveTextContent('Done · 1 tool call · 23k tokens');  // live
  expect(screen.getByRole('form', { name: 'Message box' })).toBeVisible();  // still talks to the main agent
  fireEvent.click(within(crumbs).getByRole('button', { name: 'Main agent' }));
  expect(screen.getByLabelText('Your message')).toHaveTextContent('Compare the services.');
  expect(screen.queryByLabelText('Subagent task')).toBeNull();
  fireEvent.click(agents.getByRole('button', { name: /Read the worker/ }));  // the sidebar selects too
  expect(screen.getByLabelText('Subagent task')).toHaveTextContent('Read the worker');
  fireEvent.click(agents.getByRole('button', { name: /Payments helper/ }));
  expect(screen.getByLabelText('Your message')).toBeVisible();
});

const asked = [
  event(5, 'input', { task: 'Document payments.' }),
  event(5, 'tool_call', { name: 'ask_payments', call_id: 'e1', summary: 'Map payments', arguments: { task: 'Map payments' } }),
  event(5, 'input', { task: 'Map payments', expert: { id: 'p', name: 'Payments' }, parent_call_id: 'e1' }),
  event(5, 'tool_call', { name: 'read_file', call_id: 'r1', summary: 'pay.py', parent_call_id: 'e1' }),
  event(5, 'message', { text: 'Payments map.', parent_call_id: 'e1' }),
  event(5, 'tool_result', { name: 'ask_payments', call_id: 'e1', preview: 'Payments map.', is_error: false }),
];

test('an expert call shows as Asked in the Worked block, Expert in the sidebar, and its name in the breadcrumb', () => {
  show({ run: run({ session: { ...detail, status: 'working' as const }, events: asked }) });
  expect(screen.getByText(/^Asked Payments · Map payments · 1 tool call · .* · done$/)).toBeInTheDocument();
  const agents = within(screen.getByRole('region', { name: 'Agents in this run' }));
  expect(agents.getByRole('button', { name: /Expert · Payments/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Open expert: Map payments' }));
  expect(screen.getByRole('navigation', { name: 'breadcrumb' })).toHaveTextContent(/Main agent\s*›\s*Payments: Map payments/);
});

test('a subagent that was still waiting for a slot when its turn ended shows stopped', () => {
  show({ run: run({ session: { ...detail, status: 'stopped' }, events: [
    event(6, 'input', { task: 'Compare.' }),
    event(6, 'tool_call', { name: 'run_subagent', call_id: 'w1', summary: 'Queued work', arguments: { task: 'Queued work' } }),
    event(6, 'input', { task: 'Queued work', subagent: { task: 'Queued work' }, parent_call_id: 'w1' }),
    event(6, 'end', { status: 'stopped' })] }) });
  expect(screen.getByText('Subagent · Queued work · 0 tool calls · 0 tokens · stopped')).toBeInTheDocument();
  const agents = within(screen.getByRole('region', { name: 'Agents in this run' }));
  expect(agents.getByRole('button', { name: /Queued work/ })).toHaveTextContent('Stopped · 0 tool calls · 0 tokens');
});

test('the drawer lists the subagents too; picking one shows it and closes the drawer', async () => {
  show({ run: run({ session: { ...detail, status: 'working' }, events: delegated }) });
  fireEvent.click(screen.getByRole('button', { name: 'Run details' }));
  const drawer = within(screen.getByRole('dialog'));
  fireEvent.click(within(drawer.getByRole('region', { name: 'Agents in this run' })).getByRole('button', { name: /Read the worker/ }));
  expect(screen.getByLabelText('Subagent task')).toHaveTextContent('Read the worker');
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

function sized(pane: HTMLElement, scrollHeight: number, clientHeight: number) {
  Object.defineProperty(pane, 'scrollHeight', { configurable: true, get: () => scrollHeight });
  Object.defineProperty(pane, 'clientHeight', { configurable: true, get: () => clientHeight });
  Object.defineProperty(pane, 'scrollTop', { configurable: true, writable: true, value: 0 });
}
const workspace = (change: Partial<SessionRun>) => <MemoryRouter><RunWorkspace run={run(change)}
  agentName="Payments helper" resources="Fixed" composer={composer} /></MemoryRouter>;

test('while a subagent is shown, earlier pages are not loaded and the paging lines are hidden', () => {
  const loadOlder = vi.fn(async () => {});
  const working = { ...detail, status: 'working' as const };
  const view = render(workspace({ session: working, events: delegated, older: 'more', loadOlder }));
  fireEvent.click(screen.getByRole('button', { name: 'Open subagent: Read the worker' }));
  sized(screen.getByTestId('run-scroll'), 100, 500);  // a short subagent: nothing to scroll
  loadOlder.mockClear();
  view.rerender(workspace({ session: working, events: [...delegated, event(5, 'reasoning', { text: 'More.', parent_call_id: 's1' })], older: 'loading', loadOlder }));
  fireEvent.scroll(screen.getByTestId('run-scroll'));
  expect(loadOlder).not.toHaveBeenCalled();
  expect(screen.queryByText('Loading earlier activity…')).toBeNull();
  view.rerender(workspace({ session: working, events: delegated, older: 'start', loadOlder }));
  expect(screen.queryByText('Beginning of the session')).toBeNull();
});

test('switching between the main agent and a subagent scrolls the pane to the latest', () => {
  show({ run: run({ session: { ...detail, status: 'working' }, events: delegated }) });
  const pane = screen.getByTestId('run-scroll');
  sized(pane, 1000, 200);
  fireEvent.click(screen.getByRole('button', { name: 'Open subagent: Read the worker' }));
  expect(pane.scrollTop).toBe(1000);
  pane.scrollTop = 0;
  fireEvent.click(within(screen.getByRole('navigation', { name: 'breadcrumb' })).getByRole('button', { name: 'Main agent' }));
  expect(pane.scrollTop).toBe(1000);
});

test('a shown subagent that leaves the loaded events is let go, so it does not come back later', () => {
  const working = { ...detail, status: 'working' as const };
  const view = render(workspace({ session: working, events: delegated }));
  fireEvent.click(screen.getByRole('button', { name: 'Open subagent: Read the worker' }));
  view.rerender(workspace({ session: working, events: conversation }));  // a reconnect replaced the events
  view.rerender(workspace({ session: working, events: delegated }));
  expect(screen.queryByLabelText('Subagent task')).toBeNull();
  expect(screen.getAllByLabelText('Your message')[0]).toHaveTextContent('Compare the services.');
});

test('a subagent with no task yet is named the same way everywhere', () => {
  show({ run: run({ session: { ...detail, status: 'working' }, events: [
    event(7, 'input', { task: 'Go.' }), event(7, 'tool_call', { name: 'run_subagent', call_id: 'n1', summary: '' })] }) });
  expect(screen.getByText('Subagent · no task yet · 0 tool calls · 0 tokens · working')).toBeInTheDocument();
  expect(within(screen.getByRole('region', { name: 'Agents in this run' })).getByRole('button', { name: /^Subagent/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Open subagent: no task yet' }));
  expect(screen.getByRole('navigation', { name: 'breadcrumb' })).toHaveTextContent(/Subagent: no task yet/);
  expect(screen.getByLabelText('Subagent task')).toHaveTextContent('no task yet');
});

test('a tool named ask_* that is not an expert is a plain tool line', () => {
  show({ run: run({ session: { ...detail, status: 'working' as const }, events: [
    event(5, 'input', { task: 'Hi' }),
    event(5, 'tool_call', { name: 'ask_question', call_id: 'q1', summary: 'why' }),
    event(5, 'tool_result', { name: 'ask_question', call_id: 'q1', preview: 'because', is_error: false })] }) });
  expect(screen.queryByRole('button', { name: /^Open / })).toBeNull();
  expect(screen.queryByText(/^Asked /)).toBeNull();
});
