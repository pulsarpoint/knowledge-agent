import type { SessionEvent } from "./api";

/** The tool that starts a subagent (its events carry a parent_call_id). */
const SUBAGENT_TOOL = "run_subagent";

export type TurnStatus = "working" | "completed" | "failed" | "stopped";
export type SubagentStatus = "working" | "done" | "failed" | "stopped";
export interface WorkItem {
  id: number;
  kind: "reasoning" | "tool" | "comment" | "message";
  text: string;
  name?: string;
  summary?: string;
  callId?: string;
  preview?: string;
  isError?: boolean;
}
export interface TurnUsage {
  input: number;
  output: number;
  cached: number;
  reasoning: number;
  total: number;
  calls: number;
}
/** A helper the agent started with run_subagent (its events carry the call id as parent_call_id): its task, its own
 * work and answer, its usage and tool calls, and its status — working until the agent's run_subagent result arrives,
 * then done, or failed when that result is an error; stopped when the turn ended first. */
export interface Subagent {
  callId: string;
  turn: number;
  firstId: number;
  task: string;
  work: WorkItem[];
  toolCalls: number;
  answer: string | null;
  status: SubagentStatus;
  error: string | null;
  usage: TurnUsage;
  /** Set when the helper is an expert agent the caller asked (ask_<name>), not a plain subagent. */
  expert: { id: string; name: string } | null;
}
export interface Turn {
  turn: number;
  firstId: number;
  input: string | null;
  work: WorkItem[];
  toolCalls: number;
  answer: string | null;
  status: TurnStatus;
  error: string | null;
  usage: TurnUsage;
  subagents: Subagent[];
}
export const SUBAGENT_STATUS_LABELS: Record<SubagentStatus, string> = {
  working: "Working",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
};

const ENDED: Record<string, TurnStatus> = { completed: "completed", failed: "failed", stopped: "stopped" };
const text = (payload: Record<string, unknown>, key: string) =>
  typeof payload[key] === "string" ? (payload[key] as string) : "";
const amount = (payload: Record<string, unknown>, key: string) => Number(payload[key] ?? 0) || 0;
/** A run_subagent result that is a failure: the subagent failed, or the call was refused (the SDK's tool error text).
 * Not every is_error: a subagent's successful answer may be JSON with an error key. */
const SUBAGENT_FAILED = [/^Subagent failed: /, /^An error occurred while running the tool/];
/** A helper's result is a failure: the patterns above, or for an expert exactly "Expert <its name> failed: ". */
const helperFailed = (sub: Subagent, preview: string) =>
  SUBAGENT_FAILED.some((pattern) => pattern.test(preview)) ||
  (sub.expert !== null && preview.startsWith(`Expert ${sub.expert.name} failed: `));
const noUsage = (): TurnUsage => ({ input: 0, output: 0, cached: 0, reasoning: 0, total: 0, calls: 0 });

type Pending = { pending: { id: number; text: string } | null };
type Work = Pending & { work: WorkItem[]; toolCalls: number; usage: TurnUsage; error: string | null };
type BuildingSubagent = Subagent & Pending;
type BuildingTurn = Turn & Pending & { helpers: Map<string, BuildingSubagent> };

function settle(target: Work) {
  // a message followed by more work was not the answer
  if (target.pending) target.work.push({ id: target.pending.id, kind: "message", text: target.pending.text });
  target.pending = null;
}

/** The kinds a turn and a subagent share: thinking, tool calls with their results, messages, usage and errors. */
function addWork(target: Work, event: SessionEvent) {
  const p = event.payload;
  switch (event.kind) {
    case "reasoning":
      if (text(p, "text").trim()) {
        settle(target);
        target.work.push({ id: event.id, kind: "reasoning", text: text(p, "text") });
      }
      break;
    case "tool_call":
      settle(target);
      target.toolCalls += 1;
      target.work.push({
        id: event.id,
        kind: "tool",
        text: "",
        name: text(p, "name"),
        summary: text(p, "summary"),
        callId: text(p, "call_id") || undefined,
      });
      break;
    case "tool_result": {
      const callId = text(p, "call_id");
      const call = [...target.work]
        .reverse()
        .find(
          (item) =>
            item.kind === "tool" && item.preview === undefined && (!callId || !item.callId || item.callId === callId),
        );
      if (call) {
        call.preview = text(p, "preview");
        call.isError = p.is_error === true;
      }
      break;
    }
    case "message":
      settle(target);
      target.pending = { id: event.id, text: text(p, "text") };
      break;
    case "usage":
      target.usage.input += amount(p, "input_tokens");
      target.usage.output += amount(p, "output_tokens");
      target.usage.cached += amount(p, "cached_tokens");
      target.usage.reasoning += amount(p, "reasoning_tokens");
      target.usage.total += amount(p, "total_tokens");
      target.usage.calls += 1;
      break;
    case "error":
      target.error = text(p, "message") || null;
      break;
  }
}

function helper(turn: BuildingTurn, callId: string, event: SessionEvent): BuildingSubagent {
  let found = turn.helpers.get(callId);
  if (!found) {
    found = {
      callId,
      turn: turn.turn,
      firstId: event.id,
      task: "",
      work: [],
      toolCalls: 0,
      answer: null,
      status: "working",
      error: null,
      usage: noUsage(),
      expert: null,
      pending: null,
    };
    turn.helpers.set(callId, found);
  }
  return found;
}

function startSubagent(turn: BuildingTurn, callId: string, event: SessionEvent) {
  const sub = helper(turn, callId, event);
  const args = event.payload.arguments as Record<string, unknown> | undefined;
  if (!sub.task && args && typeof args.task === "string") sub.task = args.task;
}

/** The result of a call that names an existing helper settles it: done, or failed with the result as its error. */
function settleHelper(turn: BuildingTurn, callId: string, preview: string) {
  const sub = turn.helpers.get(callId);
  if (!sub) return;
  sub.status = helperFailed(sub, preview) ? "failed" : "done";
  // A refused call records no subagent events: its reason is the result.
  if (sub.status === "failed" && !sub.error) sub.error = preview || null;
}

function answered<T extends Pending>({ pending, ...rest }: T) {
  return { ...rest, answer: pending ? pending.text : null };
}

/** A session's events as turns, in order: your message (null when the loaded page starts inside the turn), the work
 * (thinking, tool calls with their results, comments you sent while it worked, messages written along the way), the
 * final answer — the last message with no thinking or tool call after it —, the status (working
 * until the turn's end event), the usage of its model calls and its subagents (events with a parent_call_id, never
 * part of the main work). Pure: the same events give the same turns. */
export function groupTurns(events: SessionEvent[]): Turn[] {
  const turns = new Map<number, BuildingTurn>();
  for (const event of events) {
    let turn = turns.get(event.turn);
    if (!turn) {
      turn = {
        turn: event.turn,
        firstId: event.id,
        input: null,
        work: [],
        toolCalls: 0,
        answer: null,
        status: "working",
        error: null,
        usage: noUsage(),
        subagents: [],
        helpers: new Map(),
        pending: null,
      };
      turns.set(event.turn, turn);
    }
    const p = event.payload;
    const parent = text(p, "parent_call_id");
    if (parent) {
      const sub = helper(turn, parent, event);
      if (event.kind === "input") {
        if (!sub.task) sub.task = text(p, "task");
        const expert = p.expert as { id?: unknown; name?: unknown } | undefined;
        if (expert && typeof expert.id === "string" && typeof expert.name === "string")
          sub.expert = { id: expert.id, name: expert.name };
      } else addWork(sub, event);
      // An expert's own run_subagent is an expert event too (parent = the expert call): it starts the nested helper and
      // its result settles it.
      const callId = text(p, "call_id");
      if (callId && event.kind === "tool_call" && text(p, "name") === SUBAGENT_TOOL) startSubagent(turn, callId, event);
      else if (callId && event.kind === "tool_result") settleHelper(turn, callId, text(p, "preview"));
      continue;
    }
    switch (event.kind) {
      case "input":
        if (p.comment) turn.work.push({ id: event.id, kind: "comment", text: text(p, "task") });
        else if (turn.input === null) turn.input = text(p, "task");
        break;
      case "end":
        turn.status = ENDED[text(p, "status")] ?? "failed";
        // Still working when the turn ended (stopped, failed, or cancelled while waiting for a slot): stopped.
        for (const sub of turn.helpers.values()) if (sub.status === "working") sub.status = "stopped";
        break;
      default: {
        addWork(turn, event);
        const callId = text(p, "call_id");
        if (!callId) break;
        // Only run_subagent starts a helper on its own call; an expert's helper starts with its input event.
        if (event.kind === "tool_call" && text(p, "name") === SUBAGENT_TOOL) startSubagent(turn, callId, event);
        else if (event.kind === "tool_result") settleHelper(turn, callId, text(p, "preview"));
      }
    }
  }
  return [...turns.values()]
    .sort((a, b) => a.firstId - b.firstId)
    .map(({ helpers, ...turn }) => ({
      ...answered(turn),
      subagents: [...helpers.values()].sort((a, b) => a.firstId - b.firstId).map((sub) => answered(sub)),
    }));
}

/** "999", "1.5k", "18k". */
export const compactTokens = (value: number) =>
  value < 1000 ? String(value) : `${Number((value / 1000).toFixed(value < 10000 ? 1 : 0))}k`;
