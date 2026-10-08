import { useState } from 'react';
import { Link } from 'react-router';
import { ApiError } from '~/lib/api';
import { closeSession, saveSessionDocument, sendMessage, sessionTypeLabel, stopSession, type SavedDocument } from '~/lib/claudeCode';
import { SAME_DOCUMENT_NAME } from '~/lib/documents';
import { runPath } from '~/lib/runs';
import { useSessionEvents, useSessionNotice } from '~/lib/useSessionEvents';
import Markdown from './Markdown';
import RunWorkspace from './RunWorkspace';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Button } from '~/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '~/components/ui/sheet';
import { Skeleton } from '~/components/ui/skeleton';

/** One Generate documents session on the run workspace: its turns, messages, Stop and Close, and in the sidebar the
 * draft (opened in a wide side panel) with Save document. The page fits the screen; the main pane scrolls on its own. */
export default function ClaudeCodeSessionView({ sessionId }: { sessionId: string }) {
  const run = useSessionEvents(sessionId);
  const { session, setSession } = run;
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const [saved, setSaved] = useState<SavedDocument | null>(null);
  const { notice, clear, stopping, sent } = useSessionNotice(session?.status);

  async function act(action: () => Promise<void>) {
    setBusy(true); run.setError(''); clear();
    try { await action(); } catch (e) { run.setError(e instanceof Error ? e.message : 'The request failed'); } finally { setBusy(false); }
  }
  const send = () => act(async () => {
    const result = await sendMessage(sessionId, message.trim());
    setMessage('');
    sent(result.working, session?.runtime);
  });
  const stop = () => act(async () => { await stopSession(sessionId); stopping(); });
  const close = () => act(async () => { await closeSession(sessionId); setSession(current => current && { ...current, status: 'closed' }); });
  async function save(updateExisting: boolean) {
    setBusy(true); run.setError(''); clear();
    try {
      const result = await saveSessionDocument(sessionId, updateExisting);
      setConfirming(false); setSaved(result);
      setSession(current => current && { ...current, document_id: result.id });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.message === SAME_DOCUMENT_NAME && !updateExisting) setConfirming(true);
      else { setConfirming(false); run.setError(e instanceof Error ? e.message : 'Could not save the document'); }
    } finally { setBusy(false); }
  }

  if (!session) return run.error ? <Alert variant="destructive"><AlertDescription>{run.error}</AlertDescription></Alert> : <Skeleton className="h-60" />;
  const closed = session.status === 'closed';
  const draftTitle = session.draft_turn ? `Draft · turn ${session.draft_turn}` : 'Draft';
  // Header 4rem + main padding 3rem: the page never grows past the screen, so the message box stays visible.
  return <div className="flex h-[calc(100dvh-7rem)] min-h-[36rem] flex-col">
    <RunWorkspace run={run} agentName={session.agent_name} agentType={sessionTypeLabel(session.runtime)}
      resources={session.run_resources ? 'Per run' : 'Fixed'} notice={notice}
      heading={<>
        <Link className="self-start underline" to="/documentation/generations">Generate documents</Link>
        <h1 className="text-2xl font-semibold">{session.title}</h1>
        <p className="text-sm text-muted-foreground">{session.model_name} · {session.folder}/</p>
      </>}
      headerActions={<Button type="button" variant="outline" size="sm" disabled={busy || closed} onClick={() => void close()}>Close session</Button>}
      sidebar={<section aria-label="Draft" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">{draftTitle}</h2>
        {!session.draft && <p className="text-xs text-muted-foreground">The draft appears when the agent finishes a turn.</p>}
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" disabled={!session.draft} onClick={() => setDraftOpen(true)}>Open draft</Button>
          <Button type="button" size="sm" disabled={busy || !session.draft} onClick={() => void save(false)}>Save document</Button>
        </div>
        {saved && <p className="text-xs">Saved as version {saved.revision} in {saved.folder}. <Link className="underline" to={`/documents/${saved.id}`}>Open document</Link>{saved.export_error ? ` ${saved.export_error}` : ''}</p>}
      </section>}
      answerActions={turn => <Button variant="ghost" size="sm" asChild><Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link></Button>}
      empty={<p className="text-sm text-muted-foreground">The agent's messages, thinking and tool calls appear here.</p>}
      composer={{ value: message, onChange: setMessage, onSend: () => void send(), onStop: () => void stop(), busy,
        blockedReason: closed ? 'This session is closed. Start a new session.' : null, label: 'Message',
        placeholder: 'For example: shorten the API section' }} />
    <Sheet open={draftOpen} onOpenChange={setDraftOpen}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader><SheetTitle>{draftTitle}</SheetTitle><SheetDescription>The agent's latest complete document. Save document adds it as a version in {session.folder}.</SheetDescription></SheetHeader>
        <div className="mt-4"><Markdown content={session.draft ?? ''} /></div>
      </SheetContent>
    </Sheet>
    <AlertDialog open={confirming} onOpenChange={setConfirming}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Save a new version?</AlertDialogTitle>
      <AlertDialogDescription>{`${session.folder} already has a document named “${session.document_name}”. Saving adds a new version of it; earlier versions stay available.`}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={e => { e.preventDefault(); void save(true); }}>Save new version</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>;
}
