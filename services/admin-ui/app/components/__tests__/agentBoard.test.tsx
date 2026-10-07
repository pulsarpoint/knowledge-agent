import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { act } from 'react';
import AgentBoardView from '../AgentBoardView';
import type { TeamTask } from '../../lib/board';

vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));
afterEach(() => vi.unstubAllGlobals());
class ActivityStream extends EventTarget {
  static instances: ActivityStream[] = [];
  close = vi.fn();
  onerror: (() => void) | null = null;
  constructor(public url: string) { super(); ActivityStream.instances.push(this); }
  emit(type: string, value = {}) { this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(value) })); }
}
beforeEach(() => {
  ActivityStream.instances = [];
  vi.stubGlobal('EventSource', ActivityStream);
});
const settings = { agent_id: 'coordinator', name: 'Coordinator', enabled: true, notes: '', model_id: 'coordinator-model', model_name: 'Coordinator model', model_enabled: true, system_prompt: 'Coordinate the selected team.', summary_prompt: 'Write a final response from the evidence.', max_turns: 8, max_subtasks: 20, timeout_seconds: 1800, revision: 1 };
const agents = [
  { id: 'coordinator', name: 'Coordinator', is_coordinator: true, enabled: true, model_id: 'coordinator-model', prompt_version: 1 },
  { id: 'code', name: 'Code specialist', enabled: true, model_id: 'one', model_name: 'Model one', prompt_version: 1 },
  { id: 'search', name: 'Search specialist', enabled: true, model_id: 'two', model_name: 'Model two', prompt_version: 2 },
  { id: 'operations', name: 'Operations specialist', enabled: true, model_id: 'two', model_name: 'Model two', prompt_version: 2 },
  { id: 'writer', name: 'Technical writer', runtime: 'claude_code', enabled: true, model_id: null, prompt_version: null },
  { id: 'off', name: 'Disabled agent', enabled: false, model_id: 'two', prompt_version: 1 },
];
const child = {
  id: 'child', parent_id: 'first', agent_id: 'search', agent_name: 'Search specialist', task: 'Find the runbook.', status: 'completed' as const,
  phase: 'done' as const, plan: '', cancel_requested: false, model_name: 'Model two', prompt_version: 2,
  result: 'Runbook found.', error: null, usage: null,
  trace_id: 'trace', span_id: 'span', trace_url: 'http://phoenix.test/redirects/spans/span',
  created_at: '2026-09-11T10:00:00Z', started_at: '2026-09-11T10:00:01Z', finished_at: '2026-09-11T10:00:05Z',
};
const task: TeamTask = {
  ...child, id: 'first', parent_id: null, agent_id: null, agent_name: 'Coordinator',
  task: 'Investigate payment retries.', status: 'running', phase: 'working', result: null,
  plan: 'Check code and the runbook.', subagents: [{ id: 'search', name: 'Search specialist' }, { id: 'operations', name: 'Operations specialist' }],
  subtasks: [child, { ...child, id: 'second', agent_id: 'operations', agent_name: 'Operations specialist', task: 'Inspect retries.', status: 'running', result: null }],
};

test('submits one main task with a team and displays subagent and combined results', async () => {
  let rows: TeamTask[] = [];
  const submitted: Record<string, unknown>[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/agent-board/settings') return Response.json(settings);
    if (url === '/api/agents') return Response.json({ agents });
    if (init?.method === 'POST') {
      const body = JSON.parse(init.body as string);
      submitted.push(body); rows = [{ ...task, id: body.id, task: body.task }];
      return Response.json({ id: body.id });
    }
    return Response.json({ tasks: rows, has_more: false });
  }));
  const user = userEvent.setup();
  render(<MemoryRouter><AgentBoardView /></MemoryRouter>);
  fireEvent.change(await screen.findByLabelText('Main task'), { target: { value: 'Investigate payment retries.' } });
  expect(screen.queryByRole('combobox', { name: 'Coordinator' })).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: 'Coordinator' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Coordinator settings' })).toHaveAttribute('href', '/agents/coordinator');
  expect(screen.getByRole('checkbox', { name: 'Code specialist' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Start task' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'Disabled agent' })).toBeDisabled();
  expect(screen.queryByRole('checkbox', { name: 'Technical writer' })).toBeNull();
  await user.click(screen.getByRole('checkbox', { name: 'Search specialist' }));
  await user.click(screen.getByRole('checkbox', { name: 'Operations specialist' }));
  await user.click(screen.getByRole('button', { name: 'Start task' }));
  expect(await screen.findByText('Check code and the runbook.')).toBeVisible();
  expect(submitted).toEqual([{ id: expect.any(String), subagent_ids: ['search', 'operations'], task: 'Investigate payment retries.' }]);
  expect(within(screen.getByRole('region', { name: 'Running' })).getByText('Operations specialist')).toBeVisible();
  await user.click(within(screen.getByRole('region', { name: 'Results' })).getByRole('button', { name: 'View work' }));
  expect(within(screen.getByRole('region', { name: 'Agent result' })).getByText('Runbook found.')).toBeVisible();
  expect(screen.getByText('Model two · Prompt v2')).toBeVisible();
  await user.keyboard('{Escape}');
  rows = [{ ...rows[0], status: 'completed', phase: 'done', result: 'Combined findings: retries follow the runbook.' }];
  await user.click(screen.getByRole('button', { name: 'Refresh board' }));
  expect(await screen.findByText('Combined findings: retries follow the runbook.')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Use task again' }));
  expect(screen.getByLabelText('Main task')).toHaveValue('Investigate payment retries.');
  expect(screen.getByRole('checkbox', { name: 'Search specialist' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Operations specialist' })).toBeChecked();
  expect(submitted).toHaveLength(1);
});

test('streams coordinator and subagent work, updates existing items and reconnects without losing previews', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => Response.json(url === '/api/agents' ? { agents } : url === '/api/agent-board/settings' ? settings : { tasks: [task], has_more: false })));
  const user = userEvent.setup();
  const view = render(<MemoryRouter><AgentBoardView /></MemoryRouter>);
  await screen.findByText('Check code and the runbook.');
  const stream = ActivityStream.instances[0];
  expect(stream.url).toBe('/api/agent-board/tasks/first/activity');
  const update = { id: 'item', task_id: 'first', kind: 'agentMessage', title: 'Agent update', detail: 'Delegating the investigation.', status: 'inProgress', time: task.created_at };
  act(() => { stream.emit('connected'); stream.emit('activity', update); });
  const coordinator = screen.getByRole('region', { name: 'Coordinator activity' });
  expect(within(coordinator).getByText('Delegating the investigation.')).toBeVisible();
  expect(within(coordinator).getByRole('link', { name: 'View trace' })).toHaveAttribute('href', task.trace_url);
  act(() => {
    stream.emit('activity', { ...update, detail: 'Reviewing findings.', status: 'completed' });
    stream.emit('activity', { ...update, task_id: 'second', kind: 'commandExecution', title: 'uname -a', detail: 'Linux fixture', status: 'completed' });
    stream.onerror?.();
  });
  expect(screen.queryByText('Delegating the investigation.')).not.toBeInTheDocument();
  expect(screen.getByText('Reviewing findings.')).toBeVisible();
  expect(screen.getByText(/Live updates interrupted/)).toBeVisible();
  await user.click(within(screen.getByRole('region', { name: 'Running' })).getByRole('button', { name: 'View work' }));
  const subagent = screen.getByRole('region', { name: 'Operations specialist activity' });
  await user.click(within(subagent).getByRole('button', { name: /uname -a/ }));
  expect(within(subagent).getByText('Linux fixture')).toBeVisible();
  expect(within(subagent).queryByText('Reviewing findings.')).not.toBeInTheDocument();
  act(() => stream.emit('connected'));
  expect(within(subagent).getByText(/Live updates connected/)).toBeVisible();
  expect(ActivityStream.instances).toHaveLength(1);
  view.unmount();
  expect(stream.close).toHaveBeenCalled();
});

test('stops the parent while keeping completed subagent results visible', async () => {
  let stopped = false;
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/agent-board/settings') return Response.json(settings);
    if (url === '/api/agents') return Response.json({ agents });
    if (init?.method === 'POST') { urls.push(url); stopped = true; return Response.json({ id: task.id }); }
    return Response.json({ tasks: [{ ...task, cancel_requested: stopped }], has_more: false });
  }));
  const user = userEvent.setup();
  render(<MemoryRouter><AgentBoardView /></MemoryRouter>);
  await user.click(await screen.findByRole('button', { name: 'Stop task' }));
  expect(await screen.findByText('Stopping…')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Stop task' })).toBeDisabled();
  expect(screen.getByText('Runbook found.')).toBeVisible();
  expect(urls).toEqual(['/api/agent-board/tasks/first/cancel']);
  expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
});

test('failed submission preserves task, team and retry ID', async () => {
  const payloads: Record<string, unknown>[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/agent-board/settings') return Response.json(settings);
    if (url === '/api/agents') return Response.json({ agents });
    if (init?.method === 'POST') {
      payloads.push(JSON.parse(init.body as string));
      return Response.json({ error: { message: 'The board is unavailable.' } }, { status: 503 });
    }
    return Response.json({ tasks: [], has_more: false });
  }));
  const user = userEvent.setup();
  render(<MemoryRouter><AgentBoardView /></MemoryRouter>);
  await user.click(await screen.findByRole('checkbox', { name: 'Search specialist' }));
  fireEvent.change(screen.getByLabelText('Main task'), { target: { value: 'Keep this task.' } });
  for (let i = 0; i < 2; i++) {
    await user.click(screen.getByRole('button', { name: 'Start task' }));
    await waitFor(() => expect(payloads).toHaveLength(i + 1));
    expect(await screen.findByRole('alert')).toHaveTextContent('The board is unavailable.');
    expect(screen.getByLabelText('Main task')).toHaveValue('Keep this task.');
    expect(screen.getByRole('checkbox', { name: 'Search specialist' })).toBeChecked();
  }
  expect(payloads[0]).toEqual(payloads[1]);
});

test('stored failures render after remount and older main tasks are accessible', async () => {
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    return Response.json(url === '/api/agents' ? { agents } : url === '/api/agent-board/settings' ? settings : {
      tasks: [{ ...task, status: 'failed', error: 'Model request failed.' }], has_more: !url.endsWith('offset=20'),
    });
  }));
  const user = userEvent.setup();
  const view = render(<MemoryRouter><AgentBoardView /></MemoryRouter>);
  expect(await screen.findByRole('alert')).toHaveTextContent('Model request failed.');
  await user.click(screen.getByRole('button', { name: 'Older tasks' }));
  await waitFor(() => expect(urls).toContain('/api/agent-board/tasks?offset=20'));
  view.unmount();
  render(<MemoryRouter><AgentBoardView /></MemoryRouter>);
  expect(await screen.findByRole('alert')).toHaveTextContent('Model request failed.');
});
