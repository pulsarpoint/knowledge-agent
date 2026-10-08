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
