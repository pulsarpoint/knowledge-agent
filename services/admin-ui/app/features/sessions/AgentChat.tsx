import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { MessageSquarePlus, Settings2 } from "lucide-react";
import {
  agentRunPath,
  closeSession,
  getSessions,
  savedIn,
  sendMessage,
  startSession,
  statusLabels,
  stopSession,
  type ChatScope,
  type RunSummary,
  type SessionDetail,
} from "./api";
import { settingsFromSession, settingsSummary, startInput, withScope, type useAgentSettings } from "./agentSettings";
import { runPath } from "./runs";
import { useFillViewport } from "~/lib/useFillViewport";
import { useSessionEvents, useSessionNotice } from "./useSessionEvents";
import AgentSettingsSheet from "./AgentSettingsSheet";
import RunSuggestions from "./RunSuggestions";
import RunWorkspace from "./RunWorkspace";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";

/** The chat for every agent run: the Ask agent pane on a document page (compact) and a saved agent's Test tab. One
 * conversation at a time (the latest open run, or none yet). The first message starts a run with the current settings
 * (a saved agent's narrowed); later messages go to it. Settings opens the settings sheet (read-only while a run is open);
 * New chat closes the run; Earlier runs opens another. A changed initialRunId (a run link) opens that run; onRunChange
 * reports the shown run (the page keeps it in the URL). Documents: confirmUnsaved wraps the first message, and
 * onDraftChange reports the run's document draft with a version that changes after each of the agent's turns. Saved
 * agents: blockedReason blocks starting a run; Add feedback, Suggestions and Review and apply tune the agent. A new document
 * (Documents → New): one conversation per page visit (no run list, no Earlier runs), started with the Title and Folder of
 * the moment (both needed); the open run shows the document it writes, and reports its draft like a document's. A server
 * (its page): discovery runs of that server; blockedReason (SSH not ready) blocks starting one; onSessionChange reports the
 * shown run with the same version (the page shows its proposals). */
const NEEDS_NAME = "Give the document a title and folder first.";
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
}: {
  scope: ChatScope;
  settings: ReturnType<typeof useAgentSettings>;
  initialRunId: string | null;
  confirmUnsaved?: (run: () => void) => void;
  onDraftChange?: (draft: SessionDetail["document_draft"] | null, version: string) => void;
  onRunChange?: (runId: string | null) => void;
  onSessionChange?: (runId: string | null, version: string) => void;
  blockedReason?: string | null;
  onAgentChanged?: () => void;
}) {
  const isAgent = scope.kind === "agent";
  const isNew = scope.kind === "new-document";
  const scopeId = scope.kind === "new-document" ? null : scope.id;
  const navigate = useNavigate();
  const [runs, setRuns] = useState<RunSummary[] | null>(isNew ? [] : null);
  const [runId, setRunId] = useState<string | null>(initialRunId);
  const [chosen, setChosen] = useState(initialRunId !== null); // the person (or the link) picked a run or New chat
  const run = useSessionEvents(runId);
  // useSessionEvents resets the session on an id change and fills it after the load: only a session of the selected run counts.
  const loaded = run.session?.id === runId ? run.session : null;
  // A run of another document (or not an Ask agent run) is not this document's: it is not shown, and its draft never reaches
  // the page. For a saved agent: a run of another agent, or not a Test or event run. A new document's runs are only the
  // ones this page started.
  const foreign =
    loaded !== null &&
    (scope.kind === "document"
      ? loaded.purpose !== "revision" || loaded.target_document_id !== scope.id
      : scope.kind === "server"
        ? loaded.purpose !== "discovery" || loaded.server_id !== scope.id
        : scope.kind === "agent" &&
          (loaded.agent_id !== scope.id || (loaded.purpose !== "test" && loaded.purpose !== "event")))
      ? loaded
      : null;
  const session = foreign ? null : loaded;
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState("");
  const [sheet, setSheet] = useState(false);
  const [confirmNew, setConfirmNew] = useState(false);
  const [suggestions, setSuggestions] = useState<{ open: boolean; turn: number | null }>({ open: false, turn: null });
  const [info, setInfo] = useState(""); // Review and apply's result
  const { notice, clear, stopping, sent } = useSessionNotice(session?.status);
  const fill = useFillViewport<HTMLElement>();

  // The run list: on mount and after a start or a close. Until the person picks, the pane opens the latest open run.
  // A failed load keeps the list unknown (no message starts a run, which could open a second one) until Retry loads it.
  const [listed, setListed] = useState(0);
  const [listFailed, setListFailed] = useState(false);
  useEffect(() => {
    if (scopeId === null) return; // a new document: no run list
    const controller = new AbortController();
    setListFailed(false);
    getSessions(
      scope.kind === "document"
        ? { documentId: scopeId }
        : scope.kind === "server"
          ? { serverId: scopeId }
          : { agentId: scopeId },
      controller.signal,
    )
      .then((value) => {
        if (controller.signal.aborted) return;
        setRuns(value.sessions);
        if (!chosen) setRunId(value.sessions.find((item) => item.status !== "closed")?.id ?? null);
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
    setMessage("");
    setRefused("");
    setInfo("");
    setSuggestions({ open: false, turn: null });
    run.setError("");
    clear();
  }
  async function act(action: () => Promise<void>): Promise<boolean> {
    setBusy(true);
    run.setError("");
    clear();
    setInfo("");
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
  // Before a run: a new document needs its Title and Folder; a saved agent may be blocked by the page.
  const blocked = isNew && (!scope.title.trim() || !scope.folder.trim()) ? NEEDS_NAME : blockedReason;
  async function send() {
    const text = message.trim();
    if (!text || !settings.options || !settings.settings || waiting || foreign || (!open && blocked)) return;
    if (open) {
      await act(async () => {
        const result = await sendMessage(runId!, text);
        setMessage("");
        sent(result.working);
      });
      return;
    }
    const { options, settings: current } = settings;
    const start = () =>
      void (async () => {
        setBusy(true);
        setRefused("");
        try {
          const started = await startSession(startInput(scope, options, current, text));
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
  const working = session?.status === "starting" || session?.status === "working";
  const reviews = session?.reviews ?? [];
  const resources = !isAgent
    ? "Per run"
    : session
      ? session.run_resources
        ? "Per run"
        : "Fixed"
      : settings.options?.agent?.resources_fixed === false
        ? "Per run"
        : "Fixed";

  return (
    <section
      aria-label={isAgent ? "Test" : "Ask agent"}
      ref={fill.ref}
      style={fill.style}
      className="flex min-w-0 flex-col gap-2"
    >
      <RunWorkspace
        compact={!isAgent}
        run={foreign ? { ...run, session: null, events: [] } : run}
        resources={resources}
        notice={notice || info}
        agentName={session?.agent_name ?? (scope.kind === "agent" ? scope.name : "Ask agent")}
        heading={
          <div className="flex flex-col gap-1">
            <h2 className="break-words font-semibold">
              {runId === null
                ? "New conversation"
                : foreign
                  ? isAgent
                    ? "Another agent's run"
                    : scope.kind === "server"
                      ? "Another server's run"
                      : "Another document's run"
                  : isNew && session
                    ? `Writing: ${session.folder ?? ""}/${session.document_name ?? ""}`
                    : (session?.title ?? "Loading the conversation…")}
            </h2>
            {foreign &&
              (isAgent ? (
                <Alert variant="destructive">
                  <AlertDescription>This run belongs to another agent.</AlertDescription>
                </Alert>
              ) : scope.kind === "server" ? (
                <Alert variant="destructive">
                  <AlertDescription>This run does not belong to this server.</AlertDescription>
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
                    : scope.kind === "server"
                      ? "Could not load this server's runs."
                      : "Could not load this document's runs."}{" "}
                  <Button type="button" variant="link" className="h-auto p-0" onClick={() => setListed((n) => n + 1)}>
                    Retry
                  </Button>
                </AlertDescription>
              </Alert>
            )}
            {runId === null && settings.options && settings.settings && (
              <p aria-label="Current settings" className="text-xs text-muted-foreground">
                {settingsSummary(settings.options, withScope(scope, settings.settings))}
              </p>
            )}
            {refused && (
              <Alert variant="destructive">
                <AlertDescription>{refused}</AlertDescription>
              </Alert>
            )}
            {settings.error && (
              <Alert variant="destructive">
                <AlertDescription>{settings.error}</AlertDescription>
              </Alert>
            )}
          </div>
        }
        headerActions={
          <>
            <Button
              type="button"
              size="sm"
              variant="outline"
              aria-label={refused ? "Settings (check the settings)" : "Settings"}
              disabled={!settings.options}
              onClick={() => setSheet(true)}
            >
              <Settings2 data-icon="inline-start" />
              Settings{refused && <span aria-hidden className="ml-1 size-2 rounded-full bg-destructive" />}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy || (runId === null && !run.error)}
              onClick={newChat}
            >
              New chat
            </Button>
            {!isNew && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button type="button" size="sm" variant="ghost" disabled={earlier.length === 0}>
                    Earlier runs
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {earlier.map((item) => (
                    <DropdownMenuItem key={item.id} onSelect={() => pick(item.id)}>
                      {`${item.title} · ${statusLabels[item.status]}${item.purpose === "event" ? " · Event" : ""}${scope.kind === "server" && (item.pending ?? 0) > 0 ? ` · ${item.pending} pending` : ""}`}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
            {isAgent && session && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!reviews.length}
                onClick={() => setSuggestions({ open: true, turn: null })}
              >
                Suggestions ({reviews.length})
              </Button>
            )}
          </>
        }
        answerActions={(turn) => (
          <>
            {(scope.kind === "document" || isNew) && !savedIn(turn) && (
              <span className="text-xs text-muted-foreground">No draft saved in this turn</span>
            )}
            {session && (
              <Button variant="ghost" size="sm" asChild>
                <Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link>
              </Button>
            )}
            {isAgent && session && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={busy || working}
                onClick={() => setSuggestions({ open: true, turn: turn.turn })}
              >
                <MessageSquarePlus data-icon="inline-start" />
                Add feedback
              </Button>
            )}
          </>
        )}
        empty={
          <p className="text-sm text-muted-foreground">
            {isAgent
              ? "Send a message to test the agent."
              : scope.kind === "server"
                ? "Ask the agent to explore this server."
                : isNew
                  ? "Tell the agent what to write."
                  : "Ask the agent to change this document."}
          </p>
        }
        composer={{
          value: message,
          onChange: setMessage,
          busy: busy || waiting || !settings.settings,
          label: "Message",
          placeholder: isAgent
            ? "Ask a question or give a task…"
            : isNew
              ? "For example: document the payments API for new developers"
              : scope.kind === "server"
                ? "For example: check the nginx configuration"
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
            ? `This run belongs to another ${isAgent ? "agent" : scope.kind === "server" ? "server" : "document"}. Start a New chat.`
            : session?.status === "closed"
              ? "This conversation is closed. Start a New chat."
              : !open
                ? blocked
                : null,
        }}
      />
      {settings.options && settings.settings && (
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
        <RunSuggestions
          agentId={scope.id}
          session={session}
          turn={suggestions.turn}
          open={suggestions.open}
          onOpenChange={(value) => setSuggestions((current) => ({ ...current, open: value }))}
          onReview={(review) =>
            run.setSession((current) => current && { ...current, reviews: [...(current.reviews ?? []), review] })
          }
          onApplied={(target, id) => {
            if (target === "clone") navigate(`/agents/${encodeURIComponent(id)}?view=test`);
            else {
              setInfo("Applied to this agent. New conversations use it.");
              onAgentChanged?.();
            }
          }}
        />
      )}
      <AlertDialog open={confirmNew} onOpenChange={setConfirmNew}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Close this conversation?</AlertDialogTitle>
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
