import type { SessionEvent } from './claudeCode';

export type TurnStatus = 'working' | 'completed' | 'failed' | 'stopped';
export interface WorkItem {
  id: number; kind: 'reasoning' | 'tool' | 'comment' | 'message'; text: string;
  name?: string; summary?: string; callId?: string; preview?: string; isError?: boolean;
}
export interface TurnUsage { input: number; output: number; cached: number; reasoning: number; total: number; calls: number }
export interface TurnSource { n: number; path: string; heading_path: string; anchor: string | null }
export interface TurnGrounding { sources: TurnSource[]; not_covered: boolean; reason?: string | null }
export interface Turn {
  turn: number; firstId: number; input: string | null; work: WorkItem[]; toolCalls: number;
  answer: string | null; grounding: TurnGrounding | null; status: TurnStatus; error: string | null; usage: TurnUsage;
}

const ENDED: Record<string, TurnStatus> = { completed: 'completed', failed: 'failed', stopped: 'stopped' };
const text = (payload: Record<string, unknown>, key: string) => typeof payload[key] === 'string' ? payload[key] as string : '';
const amount = (payload: Record<string, unknown>, key: string) => Number(payload[key] ?? 0) || 0;

/** A session's events as turns, in order: your message (null when the loaded page starts inside the turn), the work
 * (thinking, tool calls with their results, comments you sent while it worked, messages written along the way), the
 * final answer — the last message with no thinking or tool call after it — with its grounding, the status (working
 * until the turn's end event) and the usage of its model calls. Pure: the same events give the same turns. */
export function groupTurns(events: SessionEvent[]): Turn[] {
  const turns = new Map<number, Turn & { pending: { id: number; text: string } | null }>();
  for (const event of events) {
    let turn = turns.get(event.turn);
    if (!turn) {
      turn = { turn: event.turn, firstId: event.id, input: null, work: [], toolCalls: 0, answer: null, grounding: null,
        status: 'working', error: null, usage: { input: 0, output: 0, cached: 0, reasoning: 0, total: 0, calls: 0 }, pending: null };
      turns.set(event.turn, turn);
    }
    const current = turn;
    const settle = () => {  // a message followed by more work was not the answer
      if (current.pending) current.work.push({ id: current.pending.id, kind: 'message', text: current.pending.text });
      current.pending = null;
    };
    const p = event.payload;
    switch (event.kind) {
      case 'input':
        if (p.comment) current.work.push({ id: event.id, kind: 'comment', text: text(p, 'task') });
        else if (current.input === null) current.input = text(p, 'task');
        break;
      case 'reasoning':
        if (text(p, 'text').trim()) { settle(); current.work.push({ id: event.id, kind: 'reasoning', text: text(p, 'text') }); }
        break;
      case 'tool_call':
        settle();
        current.toolCalls += 1;
        current.work.push({ id: event.id, kind: 'tool', text: '', name: text(p, 'name'), summary: text(p, 'summary'),
          callId: text(p, 'call_id') || undefined });
        break;
      case 'tool_result': {
        const callId = text(p, 'call_id');
        const call = [...current.work].reverse().find(item => item.kind === 'tool' && item.preview === undefined
          && (!callId || !item.callId || item.callId === callId));
        if (call) { call.preview = text(p, 'preview'); call.isError = p.is_error === true; }
        break;
      }
      case 'message':
        settle();
        current.pending = { id: event.id, text: text(p, 'text') };
        break;
      case 'usage':
        current.usage.input += amount(p, 'input_tokens'); current.usage.output += amount(p, 'output_tokens');
        current.usage.cached += amount(p, 'cached_tokens'); current.usage.reasoning += amount(p, 'reasoning_tokens');
        current.usage.total += amount(p, 'total_tokens'); current.usage.calls += 1;
        break;
      case 'error':
        current.error = text(p, 'message') || null;
        break;
      case 'end':
        current.status = ENDED[text(p, 'status')] ?? 'failed';
        current.grounding = (p.grounding as TurnGrounding | undefined) ?? null;
        break;
    }
  }
  return [...turns.values()].sort((a, b) => a.firstId - b.firstId)
    .map(({ pending, ...turn }) => ({ ...turn, answer: pending ? pending.text : null }));
}

/** "999", "1.5k", "18k". */
export const compactTokens = (value: number) =>
  value < 1000 ? String(value) : `${Number((value / 1000).toFixed(value < 10000 ? 1 : 0))}k`;
