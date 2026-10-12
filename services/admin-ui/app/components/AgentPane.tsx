import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { Settings2 } from 'lucide-react';
import { agentRunPath, getDocumentAgentRuns, savedIn, startAgentSession, type AgentRunSummary } from '~/lib/agentSessions';
import { settingsFromSession, settingsSummary, startInput, type useAgentSettings } from '~/lib/agentSettings';
import { closeSession, sendMessage, statusLabels, stopSession, type SessionDetail } from '~/lib/generations';
import { runPath } from '~/lib/runs';
import { useFillViewport } from '~/lib/useFillViewport';
import { useSessionEvents, useSessionNotice } from '~/lib/useSessionEvents';
import AgentSettingsSheet from './AgentSettingsSheet';
import RunWorkspace from './RunWorkspace';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Button } from '~/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '~/components/ui/dropdown-menu';

/** The Ask agent pane on a document page: one conversation at a time (the latest open run, or none yet), in the compact
 * run workspace. The first message starts a run with the current settings; later messages go to it. Settings opens the
 * settings sheet (read-only while a run is open); New chat closes the run; Earlier runs opens another. A changed
 * initialRunId (a run link on the page) opens that run; onRunChange reports the shown run (the page keeps it in the URL).
 * onDraftChange reports the run's document draft with a version that changes after each of the agent's turns. */
export default function AgentPane({ documentId, documentTitle, settings, initialRunId, confirmUnsaved, onDraftChange, onRunChange }: {
  documentId: string; documentTitle: string; settings: ReturnType<typeof useAgentSettings>; initialRunId: string | null;
  confirmUnsaved: (run: () => void) => void; onDraftChange: (draft: SessionDetail['document_draft'] | null, version: string) => void;
  onRunChange?: (runId: string | null) => void }) {
  const [runs, setRuns] = useState<AgentRunSummary[] | null>(null);
  const [runId, setRunId] = useState<string | null>(initialRunId);
  const [chosen, setChosen] = useState(initialRunId !== null);  // the person (or the link) picked a run or New chat
  const run = useSessionEvents(runId);
  // useSessionEvents resets the session on an id change and fills it after the load: only a session of the selected run counts.
  const loaded = run.session?.id === runId ? run.session : null;
  // A run of another document (or not an Ask agent run) is not this document's: it is not shown, and its draft never reaches the page.
  const foreign = loaded !== null && (loaded.purpose !== 'revision' || loaded.target_document_id !== documentId) ? loaded : null;
  const session = foreign ? null : loaded;
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState('');
  const [sheet, setSheet] = useState(false);
  const [confirmNew, setConfirmNew] = useState(false);
  const { notice, clear, stopping, sent } = useSessionNotice(session?.status);
  const fill = useFillViewport<HTMLElement>();

  // The run list: on mount and after a start or a close. Until the person picks, the pane opens the latest open run.
  // A failed load keeps the list unknown (no message starts a run, which could open a second one) until Retry loads it.
  const [listed, setListed] = useState(0);
  const [listFailed, setListFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setListFailed(false);
    getDocumentAgentRuns(documentId, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setRuns(value.sessions);
      if (!chosen) setRunId(value.sessions.find(item => item.status !== 'closed')?.id ?? null);
    }).catch(() => { if (!controller.signal.aborted) setListFailed(true); });
    return () => controller.abort();
  }, [documentId, listed]);
  useEffect(() => { onDraftChange(session?.document_draft ?? null, `${session?.id}:${session?.turns}:${session?.status}`); },
    [session?.document_draft?.id, session?.document_draft?.status, session?.id, session?.turns, session?.status]);
  useEffect(() => { onRunChange?.(runId); }, [runId]);
  // A run link followed on the page: open that run. A link without a run keeps the shown run (and puts it back in the URL).
  useEffect(() => {
    if (initialRunId !== null && initialRunId !== runId) pick(initialRunId);
    else if (initialRunId !== runId) onRunChange?.(runId);
  }, [initialRunId]);

  const selected = runId !== null && !(session === null && run.error) && !foreign;  // a run is chosen (and could be loaded)
  const open = selected && session !== null && session.status !== 'closed';
  const waiting = runs === null || listFailed || (selected && session === null);  // the run list or the chosen run is still loading
  function pick(id: string | null) { setChosen(true); setRunId(id); setMessage(''); setRefused(''); run.setError(''); clear(); }
  async function act(action: () => Promise<void>): Promise<boolean> {
    setBusy(true); run.setError(''); clear();
    try { await action(); return true; }
    catch (e) { run.setError(e instanceof Error ? e.message : 'The request failed'); return false; }
    finally { setBusy(false); }
  }
  async function send() {
    const text = message.trim();
    if (!text || !settings.options || !settings.settings || waiting || foreign) return;
    if (open) {
      await act(async () => { const result = await sendMessage(runId!, text); setMessage(''); sent(result.working); });
      return;
    }
    const { options, settings: current } = settings;
    confirmUnsaved(() => void (async () => {
      setBusy(true); setRefused('');
      try {
        const started = await startAgentSession(startInput(documentId, options, current, text));
        pick(started.id); setListed(n => n + 1);
      } catch (e) { setRefused(e instanceof Error ? e.message : 'The run could not start.'); }
      finally { setBusy(false); }
    })());
  }
  /** Close the open run (if any) and show an empty conversation; false when the close failed. */
  async function closeAndClear(): Promise<boolean> {
    if (runId && session && session.status !== 'closed') {  // never a run of another document (session is null for it)
      const id = runId;
      if (!await act(async () => { await closeSession(id); })) return false;
    }
    pick(null); setListed(n => n + 1);
    return true;
  }
  const newChat = () => { if (session?.status === 'working') setConfirmNew(true); else void closeAndClear(); };
  const earlier = runs?.filter(item => item.id !== runId) ?? [];

  return <section aria-label="Ask agent" ref={fill.ref} style={fill.style} className="flex min-w-0 flex-col gap-2">
    <RunWorkspace compact run={foreign ? { ...run, session: null, events: [] } : run} agentName={session?.agent_name ?? 'Ask agent'} resources="Per run" notice={notice}
      heading={<div className="flex flex-col gap-1">
        <h2 className="break-words font-semibold">{runId === null ? 'New conversation' : foreign ? 'Another document\'s run' : session?.title ?? 'Loading the conversation…'}</h2>
        {foreign && <Alert variant="destructive"><AlertDescription>This run belongs to another document.{' '}
          {foreign.target_document_id && <Link className="underline" to={agentRunPath(foreign.target_document_id, foreign.id)}>Open it on its document</Link>}</AlertDescription></Alert>}
        {listFailed && <Alert variant="destructive"><AlertDescription>Could not load this document's runs.{' '}
          <Button type="button" variant="link" className="h-auto p-0" onClick={() => setListed(n => n + 1)}>Retry</Button></AlertDescription></Alert>}
        {runId === null && settings.options && settings.settings && <p aria-label="Current settings" className="text-xs text-muted-foreground">
          {settingsSummary(settings.options, settings.settings)}</p>}
        {refused && <Alert variant="destructive"><AlertDescription>{refused}</AlertDescription></Alert>}
        {settings.error && <Alert variant="destructive"><AlertDescription>{settings.error}</AlertDescription></Alert>}</div>}
      headerActions={<>
        <Button type="button" size="sm" variant="outline" aria-label={refused ? 'Settings (check the settings)' : 'Settings'}
          disabled={!settings.options} onClick={() => setSheet(true)}>
          <Settings2 data-icon="inline-start" />Settings{refused && <span aria-hidden className="ml-1 size-2 rounded-full bg-destructive" />}</Button>
        <Button type="button" size="sm" variant="outline" disabled={busy || (runId === null && !run.error)} onClick={newChat}>New chat</Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild><Button type="button" size="sm" variant="ghost" disabled={earlier.length === 0}>Earlier runs</Button></DropdownMenuTrigger>
          <DropdownMenuContent align="end">{earlier.map(item => <DropdownMenuItem key={item.id} onSelect={() => pick(item.id)}>
            {`${item.title} · ${statusLabels[item.status]}`}</DropdownMenuItem>)}</DropdownMenuContent>
        </DropdownMenu></>}
      answerActions={turn => <>{!savedIn(turn) && <span className="text-xs text-muted-foreground">No draft saved in this turn</span>}
        {session && <Button variant="ghost" size="sm" asChild><Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link></Button>}</>}
      empty={<p className="text-sm text-muted-foreground">Ask the agent to change this document.</p>}
      composer={{ value: message, onChange: setMessage, busy: busy || waiting || !settings.settings, label: 'Message',
        placeholder: 'For example: add a section on log rotation', onSend: () => void send(),
        onStop: open ? () => void act(async () => { await stopSession(runId!); stopping(); }) : undefined,
        blockedReason: foreign ? 'This run belongs to another document. Start a New chat.'
          : session?.status === 'closed' ? 'This conversation is closed. Start a New chat.' : null }} />
    {settings.options && settings.settings && <AgentSettingsSheet open={sheet} onOpenChange={setSheet} documentId={documentId}
      documentTitle={documentTitle} options={settings.options}
      settings={open ? settingsFromSession(settings.options, session) : settings.settings} onChange={settings.update}
      previewMessage={message} session={open ? session : null}
      onStartNew={() => { const next = settingsFromSession(settings.options!, session!); void closeAndClear().then(done => { if (done) settings.replace(next); }); }} />}
    <AlertDialog open={confirmNew} onOpenChange={setConfirmNew}>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>Close this conversation?</AlertDialogTitle>
          <AlertDialogDescription>The agent is still working. Closing stops it.</AlertDialogDescription></AlertDialogHeader>
        <AlertDialogFooter><AlertDialogCancel>Keep it</AlertDialogCancel>
          <AlertDialogAction onClick={() => void closeAndClear()}>Close and start new</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </section>;
}
