import { memo, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import type { AgentContext, ContextOptions, ChatEvent, ChatSource, CodeCitation, Transcript, EscalateEvent } from "./types";
import AdvancedContext, { DEFAULT_CONTEXT } from "./AdvancedContext";
import Markdown from "~/components/Markdown";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent } from "~/components/ui/card";
import { Empty, EmptyDescription } from "~/components/ui/empty";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupTextarea } from "~/components/ui/input-group";
import { Separator } from "~/components/ui/separator";
import { Spinner } from "~/components/ui/spinner";

const MAX_QUESTION = 10000;
// The Reader's reply when a follow-up targets a closed or expired conversation.
const CONVERSATION_ENDED = "This conversation has ended. Start a new one.";

interface Turn {
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  notCovered?: boolean;
  // The question this assistant turn answered — carried onto not-covered
  // turns so a later "Ask the code" click can escalate the same question
  // without the user retyping it.
  question?: string;
  // Set from the chat `done` event's `escalation_offered` flag: renders the
  // "Ask the code" button under a not-covered turn's copy.
  escalationOffered?: boolean;
  // True from the moment "Ask the code" is clicked onward — disables the
  // button; escalation is one-shot per turn, so this never resets to false.
  escalating?: boolean;
  // Marks a turn as the streamed answer from a code-agent escalation
  // (renders the "from code analysis" badge and any citations below).
  fromCodeAnalysis?: boolean;
  citations?: CodeCitation[];
  codeNotFound?: boolean;
  // Provenance badges: from the chat `done` event's `sources_used` while
  // streaming, and from the transcript's derived `sources_used` on reload.
  // Escalated answers still lose their "from code analysis" badge on
  // reload (no tool calls are stored for them — accepted limitation).
  sourcesUsed?: string[];
}

// Public source keys are shared by the reader and administration APIs.
const SOURCE_KEYS = ["docs", "codex"] as const;
type SourceKey = (typeof SOURCE_KEYS)[number];

// Which sources are actually toggleable for this app: capability `true` in
// AppInfo.sources. Absent `sources` (old server) means "docs only" — same
// as a present-but-docs-only capability map, both render no selector.
function availableSources(sources?: { docs: boolean; codex: boolean }): SourceKey[] {
  if (!sources) return ["docs"];
  return SOURCE_KEYS.filter((key) => sources[key]);
}

function storageKey(app: string): string {
  return `chat-sources:${app}`;
}

// Restores a persisted selection for this app, defaulting every available
// source to checked. Storage keys for sources that are no longer available
// (or never were) are ignored; a missing/corrupt entry falls back to
// all-enabled, same as a first-ever visit.
function loadSelection(app: string, available: SourceKey[]): Record<SourceKey, boolean> {
  const selection = {} as Record<SourceKey, boolean>;
  for (const key of available) selection[key] = true;
  try {
    const raw = localStorage.getItem(storageKey(app));
    if (raw) {
      const stored = JSON.parse(raw) as Record<string, boolean>;
      for (const key of available) {
        if (typeof stored[key] === "boolean") selection[key] = stored[key];
      }
    }
  } catch { /* corrupt/unavailable storage — fall back to all-enabled */ }
  return selection;
}

// The server-side sentinels the docs agent (NOT_COVERED) and the code
// agent (NOT_FOUND) answer with when they can't help. Never shown to
// users: live streams flip to the friendly panel on `done`, stored
// transcripts are mapped on load (docs agent only — see the "accepted v1
// limitation" note on the escalate handler below), and mid-stream token
// prefixes of either are held back from rendering.
const NOT_COVERED_SENTINEL = "NOT_COVERED";
const CODE_NOT_FOUND_SENTINEL = "NOT_FOUND";

function isSentinelPrefix(content: string, sentinel: string): boolean {
  const trimmed = content.trim();
  return trimmed !== "" && sentinel.startsWith(trimmed);
}

// The not-covered copy, reused verbatim (optionally prefixed) for both a
// docs-agent NOT_COVERED turn and a code-agent escalation that also came
// back not_found.
function NoDataCopy({ app, prefix }: { app: string; prefix?: string }) {
  return (
    <p>
      {prefix}
      The selected sources do not contain enough information to answer this question.
    </p>
  );
}

// The main content area of an assistant turn: the not-covered panel, the
// "also found nothing" variant for a not_found code answer, a pulsing
// placeholder while a streaming sentinel prefix is ambiguous, or the
// rendered markdown answer. A code-analysis turn checks the NOT_FOUND
// sentinel the same way the docs turn checks NOT_COVERED — only while
// `codeNotFound` is still unknown (i.e. before that turn's `done` event
// has landed) — since it's still being told apart from a real answer that
// coincidentally starts the same way.
// memo: completed turns keep their object identity across renders, so this
// skips the markdown re-parse (and mermaid work) that every keystroke in the
// input and every stream token of a LATER turn would otherwise trigger for
// the whole transcript — the 2026-08-05 typing-flicker field report.
const AssistantBody = memo(function AssistantBody({ turn, app, isStreaming }: { turn: Turn; app: string; isStreaming: boolean }) {
  if (turn.notCovered) return <NoDataCopy app={app} />;
  if (turn.fromCodeAnalysis && turn.codeNotFound) {
    return <NoDataCopy app={app} prefix="Code analysis also found nothing — " />;
  }
  const pending = turn.fromCodeAnalysis
    ? turn.codeNotFound === undefined && isSentinelPrefix(turn.content, CODE_NOT_FOUND_SENTINEL)
    : isSentinelPrefix(turn.content, NOT_COVERED_SENTINEL);
  if (pending) return <p className="text-sm text-muted-foreground animate-pulse">…</p>;
  const content = turn.sources && turn.sources.length > 0
    ? linkifySourceRefs(turn.content, turn.sources)
    : turn.content;
  return <Markdown content={content} isStreaming={isStreaming} />;
});

// Extracts a single string field out of a tool-call argument blob, which is
// itself a JSON string (e.g. '{"query": "refunds"}'). Returns undefined on
// parse failure or a missing/non-string field so callers can fall back to a
// bare verb rather than ever rendering raw JSON.
function parseJsonField(argument: string, field: string): string | undefined {
  try {
    const parsed = JSON.parse(argument) as Record<string, unknown>;
    const value = parsed?.[field];
    return typeof value === "string" ? value : undefined;
  } catch {
    return undefined;
  }
}

// Human phrasing for the activity line, per tool. Unknown tools fall back to
// their bare name; known tools whose argument fails to parse fall back to the
// bare verb (no raw JSON is ever rendered).
function describeToolActivity(name: string, argument: string): string {
  if (name === "search_docs" || name === "search_documents") {
    const query = parseJsonField(argument, "query");
    return query !== undefined ? `searching: ${query}` : "searching";
  }
  if (name === "read_doc" || name === "read_document") {
    const path = parseJsonField(argument, "path");
    return path !== undefined ? `reading: ${path}` : "reading";
  }
  if (name === "list_docs") return "listing documents";
  if (name === "validate_mermaid") return "checking diagram syntax";
  if (name.startsWith("codex:")) {
    // ask_code forwards code-agent frames with the plain-text detail as
    // `argument`. Interim agent messages ARE the narrative — show them
    // bare under a codex label; other frames keep their kind visible.
    const kind = name.slice("codex:".length);
    if (kind === "agent_message") return argument ? `codex: ${argument}` : "codex";
    return argument ? `codex ${kind}: ${argument}` : `codex ${kind}`;
  }
  return name;
}

// Code-agent tool events carry a plain-text `detail` (already truncated
// server-side) rather than the docs agent's JSON argument blob, so this
// mirrors describeToolActivity's phrasing without the JSON parsing step.
function describeCodeToolActivity(name: string, detail: string): string {
  if (name === "agent_message") return detail ? `codex: ${detail}` : "codex";
  return detail ? `${name}: ${detail}` : name;
}

// Rewrites `[n]` markers in a completed answer into markdown links pointing
// at the matching sources-panel entry (`#chat-src-<n>`), so inline citations
// are clickable. Conservative on purpose: only touches `[n]` where n matches
// an existing source's `n` — anything else (unmatched numbers, non-numeric
// brackets) passes through untouched.
function linkifySourceRefs(content: string, sources: ChatSource[]): string {
  if (sources.length === 0) return content;
  const validNs = new Set(sources.map((s) => s.n));
  let fence: string | null = null;
  return content.split(/(?<=\n)/).map((line) => {
    const marker = line.replace(/\r?\n$/, "").match(/^\s*(?:>\s*)*(?:[-+*]\s+|\d+[.)]\s+)?(`{3,}|~{3,})(.*)$/);
    if (fence !== null) {
      if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null;
      return line;
    }
    if (marker) { fence = marker[1]; return line; }
    return line.replace(/`+[^`]*`+|\[(\d+)\]/g, (match, numStr: string | undefined) => {
      const n = Number(numStr);
      return numStr !== undefined && validNs.has(n) ? `[\\[${n}\\]](#chat-src-${n})` : match;
    });
  }).join("");
}

// A row of checkboxes — one per available source — above the input. Purely
// a testing aid (per the design spec), so it's kept visually quiet: small
// text, no card/border. Renders nothing when there's zero or one available
// source (e.g. old server, or docs-only), matching today's look.
function SourceSelector({ available, selection, onToggle }: {
  available: SourceKey[];
  selection: Record<SourceKey, boolean>;
  onToggle: (key: SourceKey) => void;
}) {
  if (available.length <= 1) return null;
  const checkedCount = available.filter((key) => selection[key]).length;
  return (
    <div className="flex items-center gap-3 text-xs text-muted-foreground">
      {available.map((key) => {
        // Disable the sole remaining checked box so the user can't uncheck
        // it — the server 400s a request that disables every available
        // source, and the UI prevents that request rather than surface it.
        const isLastChecked = selection[key] && checkedCount === 1;
        return (
          <label key={key} className="flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={selection[key] ?? false}
              disabled={isLastChecked}
              onChange={() => onToggle(key)}
              className="size-3.5 accent-primary"
            />
            {key === "docs" ? "RAG" : "Source code"}
          </label>
        );
      })}
    </div>
  );
}

export interface ChatPanelProps {
  app: string;
  askEnabled: boolean;
  askReason: string | null;
  sessionKey?: string;
  onSession: (key: string) => void;
  sources?: { docs: boolean; codex: boolean };
  contextEnabled?: boolean;
  client: {
    streamChat: (app: string, question: string, session?: string, sources?: Record<string, boolean>, context?: AgentContext) => AsyncGenerator<ChatEvent>;
    getChat: (app: string, key: string) => Promise<Transcript>;
    getContext?: (app: string) => Promise<ContextOptions>;
    escalateStream?: (app: string, key: string, question: string) => AsyncGenerator<EscalateEvent>;
  };
  onRecent?: (app: string, entry: { key: string; firstQuestion: string; ts: number }) => void;
  documentHref?: (source: ChatSource) => string;
}

export default function ChatPanel({ app, askEnabled, askReason, sessionKey, onSession, sources, contextEnabled = false, client, onRecent, documentHref }: ChatPanelProps) {
  const { streamChat, getChat, escalateStream } = client;
  const addRecent = onRecent ?? (() => {});
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [activity, setActivity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [context, setContext] = useState<AgentContext>(DEFAULT_CONTEXT);
  const [contextOptions, setContextOptions] = useState<ContextOptions | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextRevision, setContextRevision] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(!!sessionKey);
  useEffect(() => {
    if (!contextEnabled || !client.getContext) return;
    let stale = false;
    setContextLoading(true);
    setContextError(null);
    client.getContext(app).then((result) => {
      if (!stale) setContextOptions(result);
    }).catch((reason) => {
      if (!stale) setContextError(reason instanceof Error ? reason.message : "Unable to load context.");
    }).finally(() => { if (!stale) setContextLoading(false); });
    return () => { stale = true; };
  }, [app, contextEnabled, contextRevision, client.getContext]);
  // Starts unset (not `sessionKey`) so the effect below always runs its
  // "load or reset" branch on mount, even when a sessionKey prop is present
  // from the start (deep-linked existing conversation).
  const session = useRef<string | undefined>(undefined);
  // Bumped every time the effect below actually resets the view (user
  // navigated to a different conversation while a stream was in flight, e.g.
  // via a recent link, "New conversation", or deleting the open chat).
  // submit() snapshots this at the start of each streaming loop; every event
  // handler inside that loop checks it against the live value and bails once
  // it no longer matches, so a stale `for await` iteration from an abandoned
  // submit() never writes into the freshly-reset state.
  const generation = useRef(0);

  const available = availableSources(sources);
  const [selection, setSelection] = useState<Record<SourceKey, boolean>>(
    () => loadSelection(app, available),
  );
  // Re-syncs the selection when the app's source capabilities change —
  // notably the real-world case where `sources` arrives asynchronously
  // after the initial render (the parent route fetches /api/apps in an
  // effect of its own). Re-reads localStorage each time rather than just
  // trusting the lazy initializer above, so a capability that flips
  // true→false→true within one mount doesn't strand a stale selection.
  useEffect(() => {
    if (!contextEnabled || !session.current) setSelection(loadSelection(app, availableSources(sources)));
  }, [app, sources]);

  function toggleSource(key: SourceKey) {
    setSelection((prev) => {
      const checkedCount = available.filter((k) => prev[k]).length;
      // Guard: refuse to uncheck the last remaining checked source. The
      // server 400s a request that disables every effectively-available
      // source — the UI prevents that request rather than surface it.
      if (prev[key] && checkedCount <= 1) return prev;
      const next = { ...prev, [key]: !prev[key] };
      try {
        localStorage.setItem(
          storageKey(app),
          JSON.stringify(Object.fromEntries(available.map((k) => [k, next[k]]))),
        );
      } catch { /* storage unavailable — selection still works for this session */ }
      return next;
    });
  }

  useEffect(() => {
    // sessionKey === session.current means this prop change is our own
    // onSession()-triggered URL push (see submit()) reflecting back down —
    // not a user navigating to a different conversation. Do nothing, so an
    // in-flight stream writing into this same mounted instance is undisturbed.
    if (sessionKey === session.current) return;
    generation.current += 1;
    session.current = sessionKey;
    setTurns([]);
    setError(null);
    setNotice(null);
    setActivity(null);
    setContext(DEFAULT_CONTEXT);
    setHistoryLoading(!!sessionKey);
    // Whatever the abandoned generation's stream is doing, this fresh view
    // has nothing in flight yet — don't leave the input stuck disabled
    // waiting on a background loop that (per the guard in submit()'s
    // `finally`) will no longer clear it itself.
    setBusy(false);
    if (!sessionKey) {
      setSelection(loadSelection(app, availableSources(sources)));
      return;
    }
    // session.current was just set to sessionKey above; capture it as
    // `requested` so the async callbacks below can tell whether they're
    // still the most recent request by the time they resolve. Without this,
    // a slow getChat for a conversation the user has since navigated away
    // from can resolve after a faster one and clobber the newer transcript.
    const requested = sessionKey;
    getChat(app, sessionKey)
      .then((t) => {
        if (session.current !== requested) return;
        setContext(t.context ?? DEFAULT_CONTEXT);
        if (t.sources) setSelection(t.sources);
        setHistoryLoading(false);
        setTurns(t.messages.map((m) => ({
          role: m.role === "user" ? "user" : "assistant", content: m.content,
          notCovered: m.role !== "user"
            && (m.not_covered ?? m.content.trim() === NOT_COVERED_SENTINEL),
          sourcesUsed: m.sources_used,
          sources: m.sources,
        })));
      })
      .catch((e) => {
        if (session.current !== requested) return;
        setError(e instanceof Error ? e.message : "conversation not found");
      });
  }, [app, sessionKey]);

  const invalidContext = contextEnabled && ((selection.docs && context.folders?.length === 0)
    || (selection.codex && context.repositories?.length === 0));
  const advanced = contextEnabled ? <AdvancedContext value={context} onChange={setContext}
    sources={selection} available={sources ?? { docs: true, codex: false }} onToggle={toggleSource}
    locked={busy || !!session.current || !!sessionKey} options={contextOptions} loading={contextLoading}
    error={contextError} refresh={() => setContextRevision((revision) => revision + 1)} /> : null;

  if (!askEnabled) {
    return (
      <div className="max-w-3xl flex flex-col gap-4">{advanced}<Empty>
        <EmptyDescription>{askReason ?? "Ask is not configured"}</EmptyDescription>
      </Empty></div>
    );
  }

  async function submit() {
    const q = question.trim();
    setError(null);
    setNotice(null);
    if (!q || busy || invalidContext || historyLoading) return;
    if (q.length > MAX_QUESTION) {
      setError("Question too long (10,000 character limit).");
      return;
    }
    // Snapshot this submit's generation. If the user navigates away mid-stream
    // (recent link / New conversation / delete-current), the reset effect
    // bumps generation.current and every check below starts failing, so this
    // now-stale loop stops touching state instead of writing into whatever
    // fresh turns/session the navigation set up.
    const myGeneration = generation.current;
    setBusy(true);
    setQuestion("");
    setTurns((t) => [...t, { role: "user", content: q }, { role: "assistant", content: "" }]);
    const isFirst = session.current === undefined;
    // Sent with every streamChat call, but only once there's an actual
    // selector to reflect — one or zero available sources (old server, or
    // docs-only) omits it entirely, same as before this feature existed.
    const sourcesParam = contextEnabled ? { docs: !!selection.docs, codex: !!selection.codex } : available.length > 1
      ? (Object.fromEntries(available.map((k) => [k, selection[k]])) as Record<string, boolean>)
      : undefined;
    try {
      for await (const event of streamChat(app, q, session.current, sourcesParam, contextEnabled ? context : undefined)) {
        if (generation.current !== myGeneration) break;
        if (event.type === "todo") {
          setNotice(event.text);
          setQuestion(q);
          setTurns((current) => current.slice(0, -2));
        } else if (event.type === "session" && !session.current) {
          // Guarded on !session.current: a session event mid-conversation
          // (after the first turn already assigned a key) is contractually
          // impossible, but the guard keeps a stray one from re-pointing
          // the recent-list entry's firstQuestion at a later turn.
          session.current = event.key;
          // addRecent before onSession: chat.tsx's onSession bridges the
          // just-created session by reading this localStorage entry back
          // out synchronously — it has to already be written by the time
          // onSession's callback runs, or the new conversation won't show
          // up in the sidebar until the next server refresh.
          addRecent(app, { key: event.key, firstQuestion: q, ts: Date.now() });
          onSession(event.key);
        } else if (event.type === "tool") {
          setActivity(describeToolActivity(event.name, event.argument));
        } else if (event.type === "token") {
          setActivity(null);
          setTurns((t) => {
            const last = t[t.length - 1];
            // No-op unless the placeholder assistant turn this stream is
            // meant to fill is still there — guards the same "turns got
            // reset out from under an in-flight stream" case as the
            // generation check above, in case anything ever slips past it.
            if (!last || last.role !== "assistant") return t;
            const next = [...t];
            next[next.length - 1] = { ...last, content: last.content + event.text };
            return next;
          });
        } else if (event.type === "sources") {
          setTurns((t) => {
            const last = t[t.length - 1];
            if (!last || last.role !== "assistant") return t;
            const next = [...t];
            next[next.length - 1] = { ...last, sources: event.sources };
            return next;
          });
        } else if (event.type === "done") {
          setTurns((t) => {
            const last = t[t.length - 1];
            if (!last || last.role !== "assistant") return t;
            const next = [...t];
            next[next.length - 1] = {
              ...last, content: event.text, notCovered: event.not_covered,
              question: q, escalationOffered: event.escalation_offered && Boolean(escalateStream),
              sourcesUsed: event.sources_used,
            };
            return next;
          });
          if (isFirst && session.current) {
            addRecent(app, { key: session.current, firstQuestion: q, ts: Date.now() });
          }
        } else if (event.type === "error") {
          setError(event.message);
          // Only drop the placeholder assistant turn if nothing streamed
          // into it yet. A mid-stream failure (e.g. MaxTurnsExceeded) can
          // emit `error` after token events — keep the partial answer
          // visible alongside the error banner rather than erasing it.
          setTurns((t) => {
            const last = t[t.length - 1];
            if (last && last.role === "assistant" && last.content === "") {
              return t.slice(0, -1);
            }
            return t;
          });
        }
      }
    } catch (reason) {
      if (generation.current === myGeneration) {
        setError(reason instanceof Error ? reason.message : "Request failed.");
        setQuestion(q);
        setTurns((current) => current.at(-1)?.content === "" ? current.slice(0, -1) : current);
      }
    } finally {
      // Only this submit's own generation gets to clear busy/activity — an
      // abandoned generation finishing in the background must not stomp on
      // whatever the user's current (newer) submit is doing.
      if (generation.current === myGeneration) {
        setActivity(null);
        setBusy(false);
      }
    }
  }

  // Escalates the question a not-covered turn (at index `i`) answered to
  // the code agent, over the session already open for this conversation
  // (the backend requires an existing session — escalation is only ever
  // offered once one exists). Reuses submit()'s generation/busy discipline
  // — setting `busy` mid-escalation also disables the main Ask
  // button/input, so a normal question can't stream into the same "last
  // turn" this loop is writing into.
  //
  // ACCEPTED V1 LIMITATION (see task-6-report.md): the escalated answer is
  // persisted server-side as a plain user+assistant turn with no stored
  // tool calls, so on transcript reload it carries no badges — the "from
  // code analysis" badge and citations exist only in live component state.
  async function escalate(i: number) {
    const key = session.current;
    const turn = turns[i];
    if (!turn || turn.escalating || busy || !key) return;
    const q = turn.question ?? "";
    const myGeneration = generation.current;
    setError(null);
    setNotice(null);
    setBusy(true);
    setTurns((t) => {
      const next = [...t];
      if (next[i]) next[i] = { ...next[i], escalating: true };
      return [...next, { role: "assistant", content: "", fromCodeAnalysis: true }];
    });
    try {
      for await (const event of escalateStream!(app, key, q)) {
        if (generation.current !== myGeneration) break;
        if (event.type === "todo") {
          setNotice(event.text);
          setTurns((current) => {
            const next = current.slice(0, -1);
            next[i] = { ...next[i], escalating: false };
            return next;
          });
        } else if (event.type === "tool") {
          setActivity(describeCodeToolActivity(event.name, event.detail));
        } else if (event.type === "token") {
          setActivity(null);
          setTurns((t) => {
            const last = t[t.length - 1];
            if (!last || last.role !== "assistant") return t;
            const next = [...t];
            next[next.length - 1] = { ...last, content: last.content + event.text };
            return next;
          });
        } else if (event.type === "done") {
          setTurns((t) => {
            const last = t[t.length - 1];
            if (!last || last.role !== "assistant") return t;
            const next = [...t];
            next[next.length - 1] = {
              ...last, content: event.answer, citations: event.citations, codeNotFound: event.not_found,
            };
            return next;
          });
        } else if (event.type === "error") {
          setError(event.message);
          setTurns((t) => {
            // Same partial-answer-stays-visible discipline as submit()'s
            // error handling, extended: a bare sentinel prefix (e.g.
            // "NOT_F" from an interrupted NOT_FOUND stream) is exactly
            // as unusable as an empty answer here — AssistantBody's
            // `pending` check treats any sentinel-prefix content on a
            // fromCodeAnalysis turn as "still streaming" (codeNotFound
            // undefined) and would otherwise pulse the placeholder
            // forever, since codeNotFound only ever gets set by a
            // `done` event this turn now never receives.
            const last = t[t.length - 1];
            const unusable = last && last.role === "assistant"
              && (last.content === ""
                  || isSentinelPrefix(last.content, CODE_NOT_FOUND_SENTINEL));
            const dropped = unusable ? t.slice(0, -1) : t;
            // A failed escalation isn't a permanent latch — only a
            // successful one (the `done` branch above) keeps "Ask the
            // code" disabled forever. Reset it here, mirroring `busy`'s
            // reset-on-error semantics, so the user can retry.
            if (!dropped[i]?.escalating) return dropped;
            const next = [...dropped];
            next[i] = { ...next[i], escalating: false };
            return next;
          });
        }
      }
    } finally {
      if (generation.current === myGeneration) {
        setActivity(null);
        setBusy(false);
      }
    }
  }

  return (
    <div className="max-w-3xl flex flex-col gap-4">
      {turns.map((turn, i) =>
        turn.role === "user" ? (
          <div key={i} className="self-end max-w-[80%] min-w-0 break-words whitespace-pre-wrap rounded-lg bg-primary p-3 text-primary-foreground">
            {turn.content}
          </div>
        ) : (
          <Card key={i}>
            <CardContent className="flex flex-col gap-3 p-4">
              {turn.fromCodeAnalysis && (
                <Badge variant="secondary" className="self-start">from code analysis</Badge>
              )}
              {turn.sourcesUsed && turn.sourcesUsed.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {turn.sourcesUsed.map((s) => (
                    <Badge key={s} variant="secondary" className="self-start">{s === "docs" ? "RAG" : s === "codex" ? "Source code" : s}</Badge>
                  ))}
                </div>
              )}
              <AssistantBody turn={turn} app={app} isStreaming={busy && i === turns.length - 1} />
              {turn.notCovered && turn.escalationOffered && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="self-start"
                  disabled={turn.escalating || busy}
                  onClick={() => void escalate(i)}
                >
                  Ask the code
                </Button>
              )}
              {turn.citations && turn.citations.length > 0 && (
                <>
                  <Separator />
                  <ul className="flex flex-col gap-1 font-mono text-sm">
                    {turn.citations.map((c, idx) => (
                      <li key={idx}>{c.file}:{c.lines}</li>
                    ))}
                  </ul>
                </>
              )}
              {turn.sources && turn.sources.length > 0 && (
                <>
                  <Separator />
                  <ol className="flex flex-col gap-1 text-sm">
                    {turn.sources.map((s) => (
                      <li key={s.n} id={`chat-src-${s.n}`}>
                        [{s.n}]{" "}
                        <Link
                          to={documentHref ? documentHref(s) : `/${app}/doc/${s.path}${s.anchor ? `#${s.anchor}` : ""}`}
                          className="underline hover:text-primary"
                        >
                          {s.path}
                        </Link>{" "}
                        <span className="text-muted-foreground">{s.heading_path}</span>
                      </li>
                    ))}
                  </ol>
                </>
              )}
            </CardContent>
          </Card>
        ),
      )}
      {activity && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> {activity}
        </p>
      )}
      {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>
            {error}
            {error === CONVERSATION_ENDED && (<>
              {" "}<Link to={`/${app}/chat`} className="font-semibold underline">New conversation</Link>
            </>)}
          </AlertDescription>
        </Alert>
      )}
      {advanced ?? <SourceSelector available={available} selection={selection} onToggle={toggleSource} />}
      <form
        onSubmit={(e) => { e.preventDefault(); void submit(); }}
        className="flex gap-2"
      >
        {/* ring-0 override: keep the resting border only — no extra
            focus ring around the ask box when the textarea is focused */}
        <InputGroup className="flex-1 has-[[data-slot=input-group-control]:focus-visible]:ring-0">
          <InputGroupTextarea
            rows={2}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends (chat convention); Shift+Enter inserts a
              // newline via the textarea's default behavior. Also skip
              // while an IME composition is in progress, so CJK users
              // committing a composition with Enter don't accidentally
              // submit (not exercisable in jsdom).
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void submit();
              }
            }}
            placeholder="Ask about this application…"
            disabled={busy || historyLoading}
            className="max-h-40 field-sizing-content"
          />
          <InputGroupAddon align="inline-end">
            <InputGroupButton type="submit" variant="default" size="sm" disabled={busy || historyLoading || !!invalidContext}>
              Ask
            </InputGroupButton>
          </InputGroupAddon>
        </InputGroup>
      </form>
    </div>
  );
}
