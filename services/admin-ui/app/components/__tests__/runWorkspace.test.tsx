import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { expect, test, vi } from 'vitest';
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div data-testid="markdown">{content}</div> }));
import RunWorkspace, { type RunWorkspaceProps } from '../RunWorkspace';
import type { SessionDetail, SessionEvent } from '~/lib/claudeCode';
import type { SessionRun } from '~/lib/useSessionEvents';

const detail: SessionDetail = {
  id: 's1', title: 'How many retries?', runtime: 'openai', agent_id: 'custom', agent_name: 'Payments helper', model_name: 'GPT',
  status: 'waiting', status_message: '', turns: 2, input_tokens: 1300, output_tokens: 300, cached_tokens: 20, cost_usd: 0,
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
  const all: RunWorkspaceProps = { run: run(), agentName: 'Payments helper', agentType: 'Native', resources: 'Per run: App', composer, ...props };
  return render(<MemoryRouter><RunWorkspace {...all} /></MemoryRouter>);
}

test('the header shows the agent, its type and resources, the status, the tokens and the tools used', () => {
  show({ headerActions: <button type="button">History</button> });
  expect(screen.getByText('Native')).toBeVisible();
  expect(screen.getByText('Resources: Per run: App')).toBeVisible();
  expect(screen.getByText('Waiting for you')).toBeVisible();
  expect(screen.getByLabelText('Token totals')).toHaveTextContent('In 1,300 · Out 300 · Cached 20 · Model calls 2 · Reasoning 30');
  expect(screen.getByLabelText('Tools used')).toHaveTextContent('search_documents 1');
  expect(screen.getByRole('button', { name: 'History' })).toBeVisible();
});

test('the header omits Model calls for Claude Code, which records one aggregate usage per turn', () => {
  show({ run: run({ session: { ...detail, runtime: 'claude_code' } }) });
  expect(screen.getByLabelText('Token totals')).toHaveTextContent('In 1,300 · Out 300 · Cached 20 · Reasoning 30');
  expect(screen.getByLabelText('Token totals')).not.toHaveTextContent('Model calls');
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

test('a turn lists its model calls, except for Claude Code which records one aggregate usage per turn', () => {
  const view = show();
  expect(screen.getByLabelText('Turn 1 usage')).toHaveTextContent('Model calls 1');
  expect(screen.getByLabelText('Turn 1 usage')).toHaveTextContent('In 1,300 · Out 200');
  view.unmount();
  show({ run: run({ session: { ...detail, runtime: 'claude_code' } }) });
  expect(screen.getByLabelText('Turn 1 usage')).toHaveTextContent('In 1,300 · Out 200');
  expect(screen.getByLabelText('Turn 1 usage')).not.toHaveTextContent('Model calls');
  expect(screen.getByText('Worked · 1 tool call · 1.5k tokens')).toBeVisible();
});

test('the main agent in the sidebar follows the session live', () => {
  const view = show();
  const agents = within(screen.getByRole('region', { name: 'Agents in this run' }));
  expect(agents.getByText('Payments helper')).toBeVisible();
  expect(agents.getByText('Waiting for you · 1 tool call · 1.6k tokens')).toBeVisible();
  view.rerender(<MemoryRouter><RunWorkspace run={run({ session: { ...detail, status: 'working', input_tokens: 4000, output_tokens: 600 },
    toolCounts: [{ name: 'search_documents', calls: 3 }] })} agentName="Payments helper" agentType="Native" resources="Fixed" composer={composer} /></MemoryRouter>);
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
