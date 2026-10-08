import { expect, test } from 'vitest';
import { compactTokens, groupTurns } from '../turns';
import type { SessionEvent } from '../claudeCode';

let next = 0;
const event = (turn: number, kind: string, payload: Record<string, unknown> = {}): SessionEvent =>
  ({ id: ++next, turn, seq: next, kind, payload, created_at: '2026-10-08T10:00:00Z' });
const usage = (turn: number, input: number, output: number, reasoning = 0) =>
  event(turn, 'usage', { input_tokens: input, output_tokens: output, cached_tokens: 10, reasoning_tokens: reasoning, total_tokens: input + output });
const sources = [{ n: 1, path: 'payments/retries.md', heading_path: 'payments', anchor: null }];

test('a turn is your message, the work and the final answer', () => {
  const [turn] = groupTurns([
    event(1, 'start', { kind: 'test_session' }), event(1, 'input', { task: 'How many retries?' }),
    event(1, 'instructions', { text: 'You are…' }), event(1, 'reasoning', { text: 'Search first.' }),
    event(1, 'message', { text: 'Let me look.' }),
    event(1, 'tool_call', { name: 'search_documents', call_id: 'c1', summary: 'retries' }), usage(1, 1000, 200, 30),
    event(1, 'tool_result', { name: 'search_documents', call_id: 'c1', preview: '[1] Three retries.', is_error: false }),
    event(1, 'input', { task: 'Cite the runbook.', comment: true }),
    event(1, 'message', { text: 'Three retries [1].' }), usage(1, 300, 50),
    event(1, 'end', { status: 'completed', grounding: { sources, not_covered: false } }),
  ]);
  expect(turn.input).toBe('How many retries?');
  expect(turn.work.map(item => item.kind)).toEqual(['reasoning', 'message', 'tool', 'comment']);
  expect(turn.work[2]).toMatchObject({ name: 'search_documents', summary: 'retries', preview: '[1] Three retries.', isError: false });
  expect(turn.work[3].text).toBe('Cite the runbook.');
  expect([turn.answer, turn.status, turn.toolCalls, turn.error]).toEqual(['Three retries [1].', 'completed', 1, null]);
  expect(turn.usage).toEqual({ input: 1300, output: 250, cached: 20, reasoning: 30, total: 1550, calls: 2 });
  expect(turn.grounding?.sources).toEqual(sources);
});

test('a message followed by more work moves into the work; a running turn has no end', () => {
  const opening = [event(2, 'input', { task: 'Check the queue.' }), event(2, 'message', { text: 'Checking.' })];
  expect(groupTurns(opening)[0]).toMatchObject({ answer: 'Checking.', status: 'working', work: [] });
  const [turn] = groupTurns([...opening, event(2, 'tool_call', { name: 'ssh_run', summary: 'prod-1: uptime' })]);
  expect(turn.answer).toBeNull();
  expect(turn.work.map(item => [item.kind, item.text || item.name])).toEqual([['message', 'Checking.'], ['tool', 'ssh_run']]);
});

test('a page that starts in the middle of a turn, failed and stopped turns', () => {
  const turns = groupTurns([
    event(3, 'tool_result', { name: 'read_file', preview: 'orphan' }), event(3, 'message', { text: 'Partial.' }),
    event(3, 'end', { status: 'completed' }),
    event(4, 'input', { task: 'Again.' }), event(4, 'error', { message: 'Agent execution failed (RuntimeError).' }),
    event(4, 'end', { status: 'failed' }),
    event(5, 'input', { task: 'Stop me.' }), event(5, 'end', { status: 'stopped' }),
    event(6, 'input', { task: 'Odd end.' }), event(6, 'end', { status: 'mystery' }),
  ]);
  expect(turns.map(turn => [turn.turn, turn.input, turn.status])).toEqual([
    [3, null, 'completed'], [4, 'Again.', 'failed'], [5, 'Stop me.', 'stopped'], [6, 'Odd end.', 'failed']]);
  expect(turns[0]).toMatchObject({ answer: 'Partial.', work: [], grounding: null });
  expect(turns[1].error).toBe('Agent execution failed (RuntimeError).');
  expect(turns.every(turn => turn.subagents.length === 0)).toBe(true);
});

test('tool results find their calls by call id', () => {
  const [turn] = groupTurns([
    event(7, 'tool_call', { name: 'read_file', call_id: 'a', summary: 'a.py' }),
    event(7, 'tool_call', { name: 'read_file', call_id: 'b', summary: 'b.py' }),
    event(7, 'tool_result', { name: 'read_file', call_id: 'b', preview: 'B', is_error: true }),
    event(7, 'tool_result', { name: 'read_file', call_id: 'a', preview: 'A', is_error: false }),
  ]);
  expect(turn.work.map(item => [item.summary, item.preview, item.isError])).toEqual([['a.py', 'A', false], ['b.py', 'B', true]]);
  expect(turn.toolCalls).toBe(2);
});

test('compact token counts', () => {
  expect([0, 999, 1000, 1500, 18000, 18499, 123456].map(compactTokens)).toEqual(['0', '999', '1k', '1.5k', '18k', '18k', '123k']);
});

test('subagent events become subagents of their turn, not part of the main work', () => {
  const [turn] = groupTurns([
    event(1, 'input', { task: 'Compare the services.' }),
    event(1, 'tool_call', { name: 'run_subagent', call_id: 's1', summary: 'Read the worker', arguments: { task: 'Read the worker', tools: ['read_file'] } }),
    event(1, 'tool_call', { name: 'run_subagent', call_id: 's2', summary: 'Read the app', arguments: { task: 'Read the app', tools: ['read_file'] } }),
    usage(1, 100, 10),
    event(1, 'input', { task: 'Read the worker', subagent: { task: 'Read the worker' }, parent_call_id: 's1' }),
    event(1, 'input', { task: 'Read the app', subagent: { task: 'Read the app' }, parent_call_id: 's2' }),
    event(1, 'tool_call', { name: 'read_file', call_id: 'r1', summary: 'w.py', parent_call_id: 's1' }),
    event(1, 'usage', { input_tokens: 30, output_tokens: 3, total_tokens: 33, parent_call_id: 's1' }),
    event(1, 'tool_result', { name: 'read_file', call_id: 'r1', preview: 'def main', is_error: false, parent_call_id: 's1' }),
    event(1, 'message', { text: 'Worker reads w.py.', parent_call_id: 's1' }),
    event(1, 'tool_result', { name: 'run_subagent', call_id: 's1', preview: 'Worker reads w.py.', is_error: false }),
    event(1, 'error', { message: 'Subagent failed: model unavailable', parent_call_id: 's2' }),
    event(1, 'tool_result', { name: 'run_subagent', call_id: 's2', preview: 'Subagent failed: model unavailable', is_error: true }),
    event(1, 'message', { text: 'The worker reads w.py.' }), usage(1, 50, 5),
    event(1, 'end', { status: 'completed' }),
  ]);
  expect(turn.answer).toBe('The worker reads w.py.');
  expect(turn.work.map(item => item.name)).toEqual(['run_subagent', 'run_subagent']);
  expect([turn.toolCalls, turn.usage.total, turn.error]).toEqual([2, 165, null]);  // the main agent's own
  const [worker, app] = turn.subagents;
  expect(worker).toMatchObject({ callId: 's1', turn: 1, task: 'Read the worker', answer: 'Worker reads w.py.', status: 'done', toolCalls: 1, error: null });
  expect(worker.work).toMatchObject([{ kind: 'tool', name: 'read_file', summary: 'w.py', preview: 'def main', isError: false }]);
  expect(worker.usage).toMatchObject({ input: 30, output: 3, total: 33, calls: 1 });
  expect(app).toMatchObject({ callId: 's2', task: 'Read the app', status: 'failed', error: 'Subagent failed: model unavailable', answer: null });
});

test('a subagent works until its result arrives, is stopped when the turn ends first, and may start before the page', () => {
  const running = [event(2, 'input', { task: 'Watch.' }),
    event(2, 'tool_call', { name: 'run_subagent', call_id: 's9', summary: 'Watch the queue', arguments: { task: 'Watch the queue' } }),
    event(2, 'reasoning', { text: 'Polling.', parent_call_id: 's9' })];
  expect(groupTurns(running)[0].subagents).toMatchObject([{ task: 'Watch the queue', status: 'working', work: [{ kind: 'reasoning', text: 'Polling.' }] }]);
  expect(groupTurns([...running, event(2, 'end', { status: 'stopped' })])[0].subagents[0].status).toBe('stopped');
  const [late] = groupTurns([event(3, 'message', { text: 'Half done.', parent_call_id: 's3' })]);
  expect(late.subagents).toMatchObject([{ callId: 's3', task: '', answer: 'Half done.', status: 'working' }]);
  expect([late.answer, late.work]).toEqual([null, []]);
});

test('a subagent cancelled while waiting for a slot has only its input event; it is stopped once its turn ends however it ends', () => {
  for (const status of ['stopped', 'failed', 'completed']) {
    const waiting = [event(4, 'input', { task: 'Compare.' }),
      event(4, 'tool_call', { name: 'run_subagent', call_id: 'w1', summary: 'Queued', arguments: { task: 'Queued' } }),
      event(4, 'input', { task: 'Queued', subagent: { task: 'Queued' }, parent_call_id: 'w1' })];
    expect(groupTurns(waiting)[0].subagents).toMatchObject([{ callId: 'w1', task: 'Queued', status: 'working', work: [], answer: null }]);
    const [ended] = groupTurns([...waiting, event(4, 'end', { status })]);
    expect(ended.subagents).toMatchObject([{ callId: 'w1', task: 'Queued', status: 'stopped', work: [] }]);
  }
});

test('a refused run_subagent call is a failed subagent whose error is the result', () => {
  const [turn] = groupTurns([
    event(5, 'tool_call', { name: 'run_subagent', call_id: 'x1', summary: 'Too much', arguments: { task: 'Too much' } }),
    event(5, 'tool_result', { name: 'run_subagent', call_id: 'x1', preview: 'An error occurred while running the tool. This turn already started 16 subagents.', is_error: true }),
  ]);
  expect(turn.subagents).toMatchObject([{ task: 'Too much', status: 'failed', work: [],
    error: 'An error occurred while running the tool. This turn already started 16 subagents.' }]);
});

test('a run_subagent result fails the subagent only when it says so, not for a successful JSON answer with an error key', () => {
  const [turn] = groupTurns([
    event(6, 'tool_call', { name: 'run_subagent', call_id: 'j1', summary: 'Check', arguments: { task: 'Check' } }),
    event(6, 'tool_call', { name: 'run_subagent', call_id: 'j2', summary: 'Fail', arguments: { task: 'Fail' } }),
    event(6, 'tool_result', { name: 'run_subagent', call_id: 'j1', preview: '{"error": null, "exit_code": 2}', is_error: true }),
    event(6, 'tool_result', { name: 'run_subagent', call_id: 'j2', preview: 'Subagent failed: timeout', is_error: false }),
  ]);
  expect(turn.subagents.map(sub => [sub.callId, sub.status, sub.error])).toEqual([['j1', 'done', null], ['j2', 'failed', 'Subagent failed: timeout']]);
});
