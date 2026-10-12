import { useState } from 'react';
import { Link, Navigate } from 'react-router';
import { draftPath } from '~/lib/drafts';
import { closeSession, saveSessionDraft, sendMessage, stopSession } from '~/lib/generations';
import { runPath } from '~/lib/runs';
import { useFillViewport } from '~/lib/useFillViewport';
import { useSessionEvents, useSessionNotice } from '~/lib/useSessionEvents';
import Markdown from './Markdown';
import RunWorkspace from './RunWorkspace';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '~/components/ui/sheet';
import { Skeleton } from '~/components/ui/skeleton';

/** One Generate documents session on the run workspace: its turns, messages, Stop and Close, and in the sidebar the
 * draft (opened in a wide side panel) with Open draft. The page fits the screen; the main pane scrolls on its own. */
export default function GenerationView({ sessionId }: { sessionId: string }) {
  const run = useSessionEvents(sessionId);
  const { session, setSession } = run;
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const { notice, clear, stopping, sent } = useSessionNotice(session?.status);
  const fill = useFillViewport<HTMLDivElement>();

  async function act(action: () => Promise<void>) {
    setBusy(true); run.setError(''); clear();
    try { await action(); } catch (e) { run.setError(e instanceof Error ? e.message : 'The request failed'); } finally { setBusy(false); }
  }
  const send = () => act(async () => {
    const result = await sendMessage(sessionId, message.trim());
    setMessage('');
    sent(result.working);
  });
  const stop = () => act(async () => { await stopSession(sessionId); stopping(); });
  const close = () => act(async () => { await closeSession(sessionId); setSession(current => current && { ...current, status: 'closed' }); });
  const saveDraft = () => act(async () => {
    const result = await saveSessionDraft(sessionId);
    setSession(current => current && { ...current, document_draft: result.draft });
  });

  if (!session) return run.error ? <Alert variant="destructive"><AlertDescription>{run.error}</AlertDescription></Alert> : <Skeleton className="h-60" />;
  // A Test or event session belongs to its agent's Test tab (an old link, or one from Agent runs).
  if (session.purpose === 'test' || session.purpose === 'event') return <Navigate replace
    to={`/agents/${encodeURIComponent(session.agent_id ?? '')}?view=test&session_id=${encodeURIComponent(session.id)}`} />;
  if (session.purpose === 'discovery' && session.server_id) return <Navigate replace
    to={`/servers/${encodeURIComponent(session.server_id)}/discovery/${encodeURIComponent(session.id)}`} />;
  const closed = session.status === 'closed';
  const draftTitle = session.draft_turn ? `Draft · turn ${session.draft_turn}` : 'Draft';
  // The page fills the screen below the app header and never grows past it, so the message box stays visible.
  return <div ref={fill.ref} style={fill.style} className="flex min-w-0 flex-col">
    <RunWorkspace run={run} agentName={session.agent_name}
      resources={session.run_resources ? 'Per run' : 'Fixed'} notice={notice}
      heading={<>
        <Link className="self-start underline" to="/documentation/generations">Generate documents</Link>
        <h1 className="break-words text-2xl font-semibold">{session.title}</h1>
        <p className="text-sm text-muted-foreground">{session.model_name} · {session.folder}/</p>
      </>}
      headerActions={<Button type="button" variant="outline" size="sm" disabled={busy || closed} onClick={() => void close()}>Close session</Button>}
      sidebar={<section aria-label="Draft" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">{draftTitle}</h2>
        {!session.draft && <p className="text-xs text-muted-foreground">The draft appears when the agent finishes a turn.</p>}
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" disabled={!session.draft} onClick={() => setDraftOpen(true)}>Preview draft</Button>
          {session.document_draft
            ? <Button asChild size="sm"><Link to={draftPath(session.document_draft)}>Open draft</Link></Button>
            : session.draft ? <Button type="button" size="sm" disabled={busy} onClick={() => void saveDraft()}>Save as draft</Button> : null}
        </div>
        {session.document_draft && <p className="text-xs text-muted-foreground">Each finished turn updates this draft until someone saves it. Promote it on its page to make it the live document.</p>}
      </section>}
      answerActions={turn => <Button variant="ghost" size="sm" asChild><Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link></Button>}
      empty={<p className="text-sm text-muted-foreground">The agent's messages, thinking and tool calls appear here.</p>}
      composer={{ value: message, onChange: setMessage, onSend: () => void send(), onStop: () => void stop(), busy,
        blockedReason: closed ? 'This session is closed. Start a new session to continue.' : null, label: 'Message',
        placeholder: 'For example: shorten the API section' }} />
    <Sheet open={draftOpen} onOpenChange={setDraftOpen}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader><SheetTitle>{draftTitle}</SheetTitle><SheetDescription>The agent's latest complete document. It is kept as a document draft; open the draft to compare it with the live version and promote it.</SheetDescription></SheetHeader>
        <div className="mt-4"><Markdown content={session.draft ?? ''} /></div>
      </SheetContent>
    </Sheet>
  </div>;
}
