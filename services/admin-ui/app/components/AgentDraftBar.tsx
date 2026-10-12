import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import { ApiError } from '~/lib/api';
import { getDraft, promoteDraft, STALE_BASE, type DraftDetail, type PromoteResult } from '~/lib/drafts';
import type { SessionDetail } from '~/lib/generations';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';

/** Above the editor while the Ask agent pane's run has an open draft: its base version and stale badge, Compare (the
 * Drafts tab with that draft) and Promote (with the stale check: the server's message, then Promote anyway). The agent may
 * save into the same draft again: the bar reloads it when `version` changes (after each of the agent's turns), and Promote
 * reads the draft's revision fresh. */
export default function AgentDraftBar({ draft, version, confirm, onPromoted }: { draft: NonNullable<SessionDetail['document_draft']>;
  version?: string; confirm: (run: () => void) => void; onPromoted: (result: PromoteResult) => void }) {
  const [, setParams] = useSearchParams();
  const [detail, setDetail] = useState<DraftDetail | null>(null);
  const [stale, setStale] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    getDraft(draft.id, controller.signal).then(d => { if (!controller.signal.aborted) { setDetail(d); setError(''); } })
      .catch(() => { if (!controller.signal.aborted) setError('Could not load the agent draft.'); });
    return () => controller.abort();
  }, [draft.id, draft.status, version]);
  async function promote(confirmStale: boolean) {
    if (!detail) return;
    setBusy(true); setError('');
    try {
      const fresh = await getDraft(draft.id);  // the agent may have saved again since the bar loaded it
      setDetail(fresh);
      onPromoted(await promoteDraft(fresh.id, fresh.revision, confirmStale)); setStale('');
    }
    catch (e) {
      if (e instanceof ApiError && e.code === STALE_BASE) setStale(e.message);
      else setError(e instanceof Error ? e.message : 'Could not promote the draft.');
    } finally { setBusy(false); }
  }
  if (draft.status !== 'open') return null;
  return <section aria-label="Agent draft" className="mb-4 flex flex-col gap-2 rounded-lg border bg-muted/40 p-3">
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span>{detail ? `Agent draft · based on version ${detail.base_version ?? '?'}` : 'Agent draft'}</span>
      {detail?.stale && <Badge variant="destructive">stale</Badge>}
      <span className="ml-auto flex gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => setParams({ tab: 'drafts', draft: draft.id }, { replace: true })}>Compare</Button>
        <Button type="button" size="sm" disabled={busy || !detail || stale !== ''} onClick={() => confirm(() => void promote(false))}>Promote</Button>
      </span>
    </div>
    {stale && <Alert variant="destructive"><AlertDescription className="flex flex-col gap-2"><span>{stale}</span><span className="flex gap-2">
      <Button type="button" size="sm" variant="destructive" disabled={busy} onClick={() => void promote(true)}>Promote anyway</Button>
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setStale('')}>Cancel</Button></span></AlertDescription></Alert>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
  </section>;
}
