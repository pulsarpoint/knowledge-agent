import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { askCode, getCodeStatus, type CodeCitation, type CodeStatus } from '../lib/codeAgent';
import Markdown from './Markdown';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '~/components/ui/field';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Spinner } from '~/components/ui/spinner';
import { Textarea } from '~/components/ui/textarea';

export default function CodeSearchAgentView({ modelRevision = 0, agentId = "code" }: { modelRevision?: number; agentId?: string }) {
  const [status, setStatus] = useState<CodeStatus>();
  const [repository, setRepository] = useState('');
  const [question, setQuestion] = useState('');
  const [context, setContext] = useState('');
  const [answer, setAnswer] = useState('');
  const [citations, setCitations] = useState<CodeCitation[]>([]);
  const [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState('');
  const [progress, setProgress] = useState('');
  const [reload, setReload] = useState(0);
  const request = useRef<AbortController | undefined>(undefined);
  useEffect(() => {
    let live = true;
    getCodeStatus(agentId).then(value => { if (live) { setStatus(value); setError(undefined); } })
      .catch(() => { if (live) setError('Could not load code search. Try refreshing.'); });
    return () => { live = false; };
  }, [reload, modelRevision, agentId]);
  useEffect(() => () => request.current?.abort(), []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !status?.ask_enabled || !repository || !question.trim()) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError(undefined); setNotice(''); setAnswer(''); setCitations([]); setNotFound(false); setProgress('Submitting…');
    let completed = false;
    try {
      for await (const event of askCode(repository, question, context, controller.signal, agentId)) {
        if (event.type === 'todo') { setNotice(event.text ?? 'TODO'); completed = true; }
        if (event.type === 'token') setAnswer(current => current + (event.text ?? ''));
        if (event.type === 'error') throw new Error(event.message ?? 'Code search failed.');
        if (event.type === 'done') { setAnswer(event.answer ?? ''); setCitations(event.citations ?? []); setNotFound(event.not_found ?? false); completed = true; }
      }
      if (!completed) throw new Error('Code search ended before returning a complete answer.');
    } catch (reason) { setError(controller.signal.aborted ? 'Code search cancelled.' : reason instanceof Error ? reason.message : 'Code search failed.'); }
    finally { setBusy(false); setProgress(''); request.current = undefined; }
  }
  return <section className="flex flex-col gap-6" aria-label="Code search agent">
    <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex flex-wrap items-center gap-2"><Badge variant="secondary">Read-only source code</Badge>{status?.model && <Badge variant="outline">{status.model}</Badge>}<Link className="text-sm underline" to="/code-projects">Manage code projects</Link></div><Button variant="outline" disabled={busy} onClick={() => setReload(value => value + 1)}>Refresh code settings</Button></div>
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {status?.ask_reason && <Alert><AlertDescription>{status.ask_reason}</AlertDescription></Alert>}
    <Card><CardHeader><CardTitle>Test code search</CardTitle><CardDescription>Submit a test request for a selected repository. Agent execution is not implemented yet.</CardDescription></CardHeader>
      <form onSubmit={event => void submit(event)}><CardContent className="pb-6"><FieldGroup>
        <Field><FieldLabel htmlFor="code-repository">Repository</FieldLabel><Select value={repository} onValueChange={setRepository} disabled={busy || !status?.repositories.length}><SelectTrigger id="code-repository"><SelectValue placeholder="Choose a repository" /></SelectTrigger><SelectContent><SelectGroup>{status?.repositories.map(repo => <SelectItem key={repo.name} value={repo.name} disabled={!repo.path_exists}>{repo.label}{!repo.path_exists && ' (not synced)'}</SelectItem>)}</SelectGroup></SelectContent></Select></Field>
        <Field><FieldLabel htmlFor="code-question">Question</FieldLabel><Textarea id="code-question" rows={4} required maxLength={10000} disabled={busy} value={question} onChange={event => setQuestion(event.target.value)} placeholder="Where are failed payments retried, and how is the retry count configured?" /></Field>
        <Field><FieldLabel htmlFor="code-context">Additional context (optional)</FieldLabel><Textarea id="code-context" rows={3} maxLength={4000} disabled={busy} value={context} onChange={event => setContext(event.target.value)} /><FieldDescription>Context for this test only. Edit permanent instructions through the active system prompt above.</FieldDescription></Field>
      </FieldGroup></CardContent><CardFooter className="gap-2"><Button disabled={busy || !status?.ask_enabled || !status.repositories.some(repo => repo.name === repository && repo.path_exists) || !question.trim()}>{busy && <Spinner />}Ask code agent</Button>{busy && <Button type="button" variant="outline" onClick={() => request.current?.abort()}>Cancel code search</Button>}</CardFooter></form>
    </Card>
    {busy && <p role="status" className="flex items-center gap-2 text-sm"><Spinner />{progress}</p>}
    {(answer || notFound) && <Card><CardHeader><CardTitle>Code search result</CardTitle><CardDescription>{notFound ? 'The agent could not find supporting code for this question.' : 'Answer and verified repository citations.'}</CardDescription></CardHeader><CardContent className="flex flex-col gap-4"><Markdown content={answer} />{citations.map((citation, index) => <blockquote key={index} className="border-l-2 pl-4"><p className="text-sm font-medium">{citation.file}:{citation.lines}</p><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-xs">{citation.quote}</pre></blockquote>)}</CardContent></Card>}
  </section>;
}
