import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Settings2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import {
  agentRunPath,
  closeSession,
  documentWrites,
  getSessions,
  isResourceScope,
  resourceTag,
  savedIn,
  sendMessage,
  startSession,
  isWorking,
  statusLabels,
  stopSession,
  type ChatScope,
  type RunSummary,
  type SessionDetail,
} from "./api";
import {
  pickReason,
  settingsFromSession,
  settingsSummary,
  startInput,
  withScope,
  writeReason,
  type useAgentSettings,
} from "./agentSettings";
import { insertMention } from "~/features/documents/drafts";
import { runPath } from "./runs";
import { useFillViewport } from "~/lib/useFillViewport";
import { useSessionEvents, useSessionNotice } from "./useSessionEvents";
import AgentSettingsSheet from "./AgentSettingsSheet";
import ReviewSheet, { useReviewParam } from "./ReviewSheet";
import { getReviews, latestRunKey, reviewReason, sessionOfRunKey, startReview } from "./reviews";
import RunWorkspace from "./RunWorkspace";
import DebugPanel from "./DebugPanel";
import RunOutputPanel from "~/features/outputSchemas/RunOutputPanel";
import { Alert, AlertDescription } from "~/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
import { Button } from "~/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";

/** A saved agent's unsent message, kept in this browser tab per conversation (`draft:agent:<id>:<run id>`; none open
 * yet: `draft:agent:<id>`), so leaving the Chat page and coming back keeps it. Storage that fails keeps nothing. */
const draftKey = (scope: ChatScope, runId: string | null) =>
  scope.kind === "agent" ? `draft:agent:${scope.id}${runId ? `:${runId}` : ""}` : null;
function readDraft(key: string | null): string {
  try {
    return (key && sessionStorage.getItem(key)) || "";
  } catch {
    return "";
  }
}
function writeDraft(key: string | null, text: string) {
  if (!key) return;
  try {
    if (text) sessionStorage.setItem(key, text);
    else sessionStorage.removeItem(key);
  } catch {
    /* storage off */
  }
}

/** The chat for every agent run: the Ask agent pane on a document page (compact) and a saved agent's Chat tab. One
 * conversation at a time (the latest open run, or none yet). The first message starts a run with the current settings
 * (a saved agent's narrowed); later messages go to it. Settings opens the settings sheet (read-only while a run is open);
 * New chat closes the run; Earlier runs opens another. A changed initialRunId (a run link) opens that run; onRunChange
 * reports the shown run (the page keeps it in the URL). Documents: confirmUnsaved wraps the first message, and
 * onDraftChange reports the run's document draft with a version that changes after each of the agent's turns. Saved
 * agents: blockedReason blocks starting a run; Improve agent (a finished run) opens the review chat (ReviewSheet, kept in ?review=) and
 * onAgentChanged reports the reviewer's turns (it may have changed the agent). A review (Improve agent): the reviews
 * of one session; ChatColumn lists them and has New review; the first message starts the review (POST …/reviews) and
 * is sent as the person's feedback; no settings (the reviewer is pinned by the API); `inSheet` fills the Sheet instead
 * of the screen. Writing with
 * an agent (Documents → New → Write with an agent): its runs (purpose documents; on a resource, tagged with it); the
 * page lists them and has New chat, so the pane has neither; it opens the run the page hands it (initialRunId) or a new
 * conversation; without a resource it needs a picked resource (writeReason); `mention` puts a draft's reference into
 * the message box at the cursor, and onDocumentWrite reports how many document writes the shown run's events hold. A
 * server, service or environment (its page): the runs tagged with it; the page lists them and has New chat, so the pane
 * has neither New chat nor Earlier runs; `fresh` starts on a new conversation instead of the latest open run;
 * blockedReason (a server whose SSH is not ready) blocks starting one; onSessionChange reports the shown run with the
 * same version; Chat opens the latest open chat. Every chat has a Debug tab beside the conversation: what the model
 * receives (DebugPanel). */
export default function AgentChat({
  scope,
  settings,
  initialRunId,
  confirmUnsaved,
  onDraftChange,
  onRunChange,
  onSessionChange,
  blockedReason = null,
  onAgentChanged,
  fresh = false,
  mention = null,
  onDocumentWrite,
  inSheet = false,
}: {
  scope: ChatScope;
  /** Not for a review (the API pins the reviewer). */
  settings?: ReturnType<typeof useAgentSettings>;
  initialRunId: string | null;
  confirmUnsaved?: (run: () => void) => void;
  onDraftChange?: (draft: SessionDetail["document_draft"] | null, version: string) => void;
  onRunChange?: (runId: string | null) => void;
  onSessionChange?: (runId: string | null, version: string) => void;
  blockedReason?: string | null;
  onAgentChanged?: () => void;
  fresh?: boolean;
  mention?: { text: string; key: number } | null;
  onDocumentWrite?: (sessionId: string, writes: number) => void;
  inSheet?: boolean;
}) {
  const isAgent = scope.kind === "agent";
  const isReview = scope.kind === "review";
  const isNew = scope.kind === "new-document";
  const resource = isResourceScope(scope) ? scope : null;
  const tag = resource ? resourceTag({ type: resource.kind, id: resource.id }) : null;
  const writeTag = scope.kind === "new-document" && scope.resource ? resourceTag(scope.resource) : null;
  const pageChat = resource !== null || isNew || isReview; // the page lists its chats and has New chat
  const scopeId = scope.kind === "new-document" ? null : scope.id;
  const [runs, setRuns] = useState<RunSummary[] | null>(isNew ? [] : null);
  const [runId, setRunId] = useState<string | null>(initialRunId);
  const [chosen, setChosen] = useState(initialRunId !== null || fresh); // the person (or the link) picked a run or New chat
  const chosenNow = useRef(chosen); // read when the run list arrives: a pick made while it loaded wins
  chosenNow.current = chosen;
  const run = useSessionEvents(runId);
  // useSessionEvents resets the session on an id change and fills it after the load: only a session of the selected run counts.
  const loaded = run.session?.id === runId ? run.session : null;
  // A run of another document (or not an Ask agent run) is not this document's: it is not shown, and its draft never reaches
  // the page. For a saved agent: a run of another agent, or not a chat or event run. Writing: a run that is not a Write
  // with an agent run (on a resource: or without its tag). A server's, service's or environment's: a run without its
  // tag, or a Write with an agent run on it. A review: not a review of this session.
  const foreign =
    loaded !== null &&
    (scope.kind === "document"
      ? loaded.purpose !== "revision" || loaded.target_document_id !== scope.id
      : tag !== null
        ? !(loaded.tags ?? []).includes(tag) || loaded.purpose === "documents"
        : isNew
          ? loaded.purpose !== "documents" || (writeTag !== null && !(loaded.tags ?? []).includes(writeTag))
          : isReview
            ? loaded.purpose !== "review" ||
              sessionOfRunKey(String(loaded.config?.review_of ?? "")) !== sessionOfRunKey(scope.id)
            : scope.kind === "agent" &&
              (loaded.agent_id !== scope.id || (loaded.purpose !== "chat" && loaded.purpose !== "event")))
      ? loaded
      : null;
  const session = foreign ? null : loaded;
  const [message, setText] = useState(() => readDraft(draftKey(scope, initialRunId)));
  const setMessage = (text: string) => {
    setText(text);
    writeDraft(draftKey(scope, runId), text);
  };
  // Another conversation (also one the run list opens) shows its own unsent message.
  useEffect(() => {
    setText(readDraft(draftKey(scope, runId)));
  }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps
  // A mention goes into the message box at the cursor (or at the end), and the box keeps the focus after it. Each
  // mention is used once: one made before this chat mounted (New chat remounts it) belongs to another conversation.
  const input = useRef<HTMLTextAreaElement>(null);
  const usedMention = useRef(mention?.key);
  useEffect(() => {
    if (!mention || mention.key === usedMention.current) return;
    usedMention.current = mention.key;
    const box = input.current;
    const next = insertMention(
      message,
      mention.text,
      box ? { start: box.selectionStart, end: box.selectionEnd } : null,
    );
    setMessage(next.value);
    requestAnimationFrame(() => {
      box?.focus();
      box?.setSelectionRange(next.cursor, next.cursor);
    });
  }, [mention?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState("");
  const [sheet, setSheet] = useState(false);
  const [confirmNew, setConfirmNew] = useState(false);
  const setReview = useReviewParam()[1]; // the Improve agent sheet is open while the URL has ?review=
  const [view, setView] = useState<"conversation" | "debug">("conversation");
  const { notice, clear, stopping, sent } = useSessionNotice(session?.status);
  const fill = useFillViewport<HTMLElement>();
  const noSettings = isReview; // the API pins a review's configuration
  const ready = noSettings || Boolean(settings?.options && settings.settings);

  // The run list: on mount and after a start or a close. Until the person picks, the pane opens the latest open run.
  // A failed load keeps the list unknown (no message starts a run, which could open a second one) until Retry loads it.
  const [listed, setListed] = useState(0);
  const [listFailed, setListFailed] = useState(false);
  useEffect(() => {
    if (scopeId === null) return; // a new document: no run list
    const controller = new AbortController();
    setListFailed(false);
    (isReview
      ? getReviews(scopeId, controller.signal)
      : getSessions(
          scope.kind === "document"
            ? { documentId: scopeId }
            : tag !== null
              ? { tag, purpose: "resource" }
              : { agentId: scopeId },
          controller.signal,
        ).then((value) => value.sessions)
    )
      .then((value) => {
        if (controller.signal.aborted) return;
        setRuns(value);
        if (!chosenNow.current) setRunId(value.find((item) => item.status !== "closed")?.id ?? null);
      })
      .catch(() => {
        if (!controller.signal.aborted) setListFailed(true);
      });
    return () => controller.abort();
  }, [scope.kind, scopeId, listed]);
  useEffect(() => {
    const version = `${session?.id}:${session?.turns}:${session?.status}`;
    onDraftChange?.(session?.document_draft ?? null, version);
    onSessionChange?.(session?.id ?? null, version);
  }, [session?.document_draft?.id, session?.document_draft?.status, session?.id, session?.turns, session?.status]);
  useEffect(() => {
    onRunChange?.(runId);
  }, [runId]);
  const writes = documentWrites(foreign ? [] : run.events);
  useEffect(() => {
    if (session) onDocumentWrite?.(session.id, writes);
  }, [session?.id, writes]);
  // A run link followed on the page: open that run. A link without a run keeps the shown run (and puts it back in the URL).
  useEffect(() => {
    if (initialRunId !== null && initialRunId !== runId) pick(initialRunId);
    else if (initialRunId !== runId) onRunChange?.(runId);
  }, [initialRunId]);

  const selected = runId !== null && !(session === null && run.error) && !foreign; // a run is chosen (and could be loaded)
  const open = selected && session !== null && session.status !== "closed";
  const waiting = runs === null || listFailed || (selected && session === null); // the run list or the chosen run is still loading
  function pick(id: string | null) {
    setChosen(true);
    setRunId(id);
    setText(readDraft(draftKey(scope, id))); // that conversation's unsent message
    setRefused("");
    run.setError("");
    clear();
  }
  async function act(action: () => Promise<void>): Promise<boolean> {
    setBusy(true);
    run.setError("");
    clear();
    try {
      await action();
      return true;
    } catch (e) {
      run.setError(e instanceof Error ? e.message : "The request failed");
      return false;
    } finally {
      setBusy(false);
    }
  }
  // Before a run: the page may block it. A saved agent with resources needs at least one picked (none picked would give
  // it all of them); writing without a resource needs one picked (new_document needs a resource of the run).
  const blocked =
    blockedReason ??
    (settings?.options && settings.settings
      ? (pickReason(settings.options, settings.settings) ?? writeReason(scope, settings.settings))
      : null);
  async function send() {
    const text = message.trim();
    if (!text || !ready || waiting || foreign || (!open && blocked)) return;
    if (open) {
      await act(async () => {
        const result = await sendMessage(runId!, text);
        setMessage("");
        sent(result.working);
      });
      return;
    }
    if (isReview) {
      // The review starts empty; the person's feedback is its first message.
      setBusy(true);
      setRefused("");
      try {
        const started = await startReview(scope.id);
        pick(started.id);
        setListed((n) => n + 1);
        const result = await sendMessage(started.id, text);
        setMessage("");
        sent(result.working);
        toast.success("Review started.");
      } catch (e) {
        setRefused(e instanceof Error ? e.message : "The review could not start.");
      } finally {
        setBusy(false);
      }
      return;
    }
    const { options, settings: current } = settings!;
    const start = () =>
      void (async () => {
        setBusy(true);
        setRefused("");
        try {
          const started = await startSession(startInput(scope, options!, current!, text));
          writeDraft(draftKey(scope, null), ""); // sent: the new conversation starts without a draft
          pick(started.id);
          setListed((n) => n + 1);
        } catch (e) {
          setRefused(e instanceof Error ? e.message : "The run could not start.");
        } finally {
          setBusy(false);
        }
      })();
    if (confirmUnsaved) confirmUnsaved(start);
    else start();
  }
  /** Close the open run (if any) and show an empty conversation; false when the close failed. */
  async function closeAndClear(): Promise<boolean> {
    if (runId && session && session.status !== "closed") {
      // never a run of another document or agent (session is null for it)
      const id = runId;
      if (
        !(await act(async () => {
          await closeSession(id);
        }))
      )
        return false;
    }
    pick(null);
    setListed((n) => n + 1);
    return true;
  }
  const newChat = () => {
    if (session?.status === "working") setConfirmNew(true);
    else void closeAndClear();
  };
  const earlier = runs?.filter((item) => item.id !== runId) ?? [];
  const working = isWorking(session?.status);
  const cannotReview = session ? reviewReason(session) : null;

  return (
    <section
      aria-label={isAgent ? "Test" : isReview ? "Review" : "Ask agent"}
      ref={inSheet ? undefined : fill.ref}
      style={inSheet ? undefined : fill.style}
      className={inSheet ? "flex min-h-0 min-w-0 flex-1 flex-col gap-2" : "flex min-w-0 flex-col gap-2"}
    >
      <Tabs value={view} onValueChange={(value) => setView(value === "debug" ? "debug" : "conversation")}>
        <TabsList aria-label="Chat or debug">
          <TabsTrigger value="conversation">Chat</TabsTrigger>
          <TabsTrigger value="debug">Debug</TabsTrigger>
        </TabsList>
      </Tabs>
      {view === "debug" && (
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          {/* A run: its pinned configuration; before the first message: what the next start would pin. */}
          <DebugPanel
            runId={runId !== null && !foreign ? runId : null}
            input={
              runId === null && settings?.options && settings.settings
                ? startInput(scope, settings.options, settings.settings, message.trim() || "(your next message)")
                : null
            }
          />
        </div>
      )}
      {/* Kept mounted while Debug is shown (the conversation keeps its scroll and stream). */}
      <div className={view === "debug" ? "hidden" : "contents"}>
        <RunWorkspace
          compact={!isAgent}
          run={foreign ? { ...run, session: null, events: [] } : run}
          notice={notice}
          agentName={
            scope.kind === "review"
              ? `Reviewer (${scope.name})`
              : (session?.agent_name ?? (scope.kind === "agent" ? scope.name : "Ask agent"))
          }
          sidebar={
            session?.config?.output_schema != null && (
              <RunOutputPanel sessionId={session.id} status={session.status} turns={session.turns} />
            )
          }
          heading={
            <div className="flex flex-col gap-1">
              <h2 className="break-words font-semibold">
                {runId === null
                  ? isReview
                    ? "New review"
                    : "New chat"
                  : foreign
                    ? isAgent
                      ? "Another agent's run"
                      : isReview
                        ? "Another chat's review"
                        : pageChat
                          ? "Another page's chat"
                          : "Another document's run"
                    : (session?.title ?? "Loading the chat…")}
              </h2>
              {foreign &&
                (isAgent ? (
                  <Alert variant="destructive">
                    <AlertDescription>This run belongs to another agent.</AlertDescription>
                  </Alert>
                ) : isReview ? (
                  <Alert variant="destructive">
                    <AlertDescription>This review belongs to another chat.</AlertDescription>
                  </Alert>
                ) : pageChat ? (
                  <Alert variant="destructive">
                    <AlertDescription>This chat belongs to another page.</AlertDescription>
                  </Alert>
                ) : (
                  <Alert variant="destructive">
                    <AlertDescription>
                      This run belongs to another document.{" "}
                      {foreign.target_document_id && (
                        <Link className="underline" to={agentRunPath(foreign.target_document_id, foreign.id)}>
                          Open it on its document
                        </Link>
                      )}
                    </AlertDescription>
                  </Alert>
                ))}
              {listFailed && (
                <Alert variant="destructive">
                  <AlertDescription>
                    {isAgent
                      ? "Could not load this agent's runs."
                      : isReview
                        ? "Could not load this chat's reviews."
                        : resource
                          ? `Could not load this ${resource.kind}'s chats.`
                          : "Could not load this document's runs."}{" "}
                    <Button type="button" variant="link" className="h-auto p-0" onClick={() => setListed((n) => n + 1)}>
                      Retry
                    </Button>
                  </AlertDescription>
                </Alert>
              )}
              {runId === null && settings?.options && settings.settings && (
                <p aria-label="Current settings" className="text-xs text-muted-foreground">
                  {settingsSummary(settings.options, withScope(scope, settings.settings, settings.options))}
                </p>
              )}
              {refused && (
                <Alert variant="destructive">
                  <AlertDescription>{refused}</AlertDescription>
                </Alert>
              )}
              {settings?.error && (
                <Alert variant="destructive">
                  <AlertDescription>{settings.error}</AlertDescription>
                </Alert>
              )}
            </div>
          }
          headerActions={
            <>
              {!noSettings && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  aria-label={refused ? "Settings (check the settings)" : "Settings"}
                  disabled={!settings?.options}
                  onClick={() => setSheet(true)}
                >
                  <Settings2 data-icon="inline-start" />
                  Settings{refused && <span aria-hidden className="ml-1 size-2 rounded-full bg-destructive" />}
                </Button>
              )}
              {!pageChat && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy || (runId === null && !run.error)}
                  onClick={newChat}
                >
                  New chat
                </Button>
              )}
              {!pageChat && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button type="button" size="sm" variant="ghost" disabled={earlier.length === 0}>
                      Earlier runs
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {earlier.map((item) => (
                      <DropdownMenuItem key={item.id} onSelect={() => pick(item.id)}>
                        {`${item.title} · ${statusLabels[item.status]}${item.purpose === "event" ? " · Event" : ""}`}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
              {isAgent && session && (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={cannotReview !== null}
                    title={cannotReview ?? undefined}
                    onClick={() => setReview("new")}
                  >
                    <Sparkles data-icon="inline-start" />
                    Improve agent
                  </Button>
                </>
              )}
            </>
          }
          answerActions={(turn) => (
            <>
              {scope.kind === "document" && !savedIn(turn) && (
                <span className="text-xs text-muted-foreground">No draft saved in this turn</span>
              )}
              {session && (
                <Button variant="ghost" size="sm" asChild>
                  <Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link>
                </Button>
              )}
            </>
          )}
          empty={
            <p className="text-sm text-muted-foreground">
              {isAgent
                ? "Send a message to test the agent."
                : isReview
                  ? `Tell the reviewer what ${scope.name} should do differently, for example about one turn. It reads the chat, proposes where the change belongs (instructions, a skill or memory) and changes nothing until you agree.`
                  : resource
                    ? `Ask the agent about this ${resource.kind}.`
                    : isNew
                      ? "Tell the agent what to write."
                      : "Ask the agent to change this document."}
            </p>
          }
          composer={{
            value: message,
            onChange: setMessage,
            inputRef: input,
            busy: busy || waiting || !ready,
            label: "Message",
            placeholder: isAgent
              ? "Ask a question or give a task…"
              : isReview
                ? "What should the agent do differently?"
                : isNew
                  ? "For example: document the payments API for new developers"
                  : resource
                    ? resource.kind === "server"
                      ? "For example: how much disk space is free?"
                      : resource.kind === "service"
                        ? "For example: is it running?"
                        : "For example: which services run here?"
                    : "For example: add a section on log rotation",
            onSend: () => void send(),
            onStop: open
              ? () =>
                  void act(async () => {
                    await stopSession(runId!);
                    stopping();
                  })
              : undefined,
            blockedReason: foreign
              ? isReview
                ? "This review belongs to another chat. Start a New review."
                : pageChat
                  ? "This chat belongs to another page. Start a New chat."
                  : `This run belongs to another ${isAgent ? "agent" : "document"}. Start a New chat.`
              : session?.status === "closed"
                ? "This chat is closed. Start a new chat."
                : !open
                  ? blocked
                  : null,
          }}
        />
      </div>
      {settings?.options && settings.settings && (
        <AgentSettingsSheet
          open={sheet}
          onOpenChange={setSheet}
          scope={scope}
          options={settings.options}
          settings={open ? settingsFromSession(settings.options, session) : settings.settings}
          onChange={settings.update}
          previewMessage={message}
          session={open ? session : null}
          onStartNew={() => {
            const next = settingsFromSession(settings.options!, session!);
            void closeAndClear().then((done) => {
              if (done) settings.replace(next);
            });
          }}
        />
      )}
      {scope.kind === "agent" && session && (
        <ReviewSheet runKey={latestRunKey(session)} agentName={scope.name} onAgentChanged={onAgentChanged} />
      )}
      <AlertDialog open={confirmNew} onOpenChange={setConfirmNew}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Close this chat?</AlertDialogTitle>
            <AlertDialogDescription>The agent is still working. Closing stops it.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction onClick={() => void closeAndClear()}>Close and start new</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
