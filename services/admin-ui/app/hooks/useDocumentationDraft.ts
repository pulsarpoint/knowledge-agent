import { useEffect, useRef, useState } from 'react';
import { saveDocumentationDraft } from '~/lib/documents';

/** Serialize autosaves and flush before starting work. Concurrent pages cannot overwrite a newer draft. */
export function useDocumentationDraft(id: string | undefined, section: string, initial: object | undefined, value: object, ready = true) {
  const saved = useRef(JSON.stringify(initial ?? null));
  const latest = useRef(JSON.stringify(value));
  latest.current = JSON.stringify(value);
  const pending = useRef<Promise<void> | null>(null);
  const mounted = useRef(true);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  function flush(): Promise<void> {
    if (!id || !ready) return Promise.resolve();
    if (pending.current) return pending.current;
    pending.current = (async () => {
      if (mounted.current) { setError(''); setStatus('Saving…'); }
      while (saved.current !== latest.current) {
        const snapshot = latest.current;
        await saveDocumentationDraft(id, section, JSON.parse(snapshot), JSON.parse(saved.current));
        saved.current = snapshot;
      }
      if (mounted.current) setStatus('Saved');
    })().catch(e => {
      if (mounted.current) { setStatus('Not saved'); setError(e instanceof Error ? e.message : 'Could not save workflow draft'); }
      throw e;
    }).finally(() => { pending.current = null; });
    return pending.current;
  }
  const serialized = latest.current;
  useEffect(() => {
    if (!id || !ready || saved.current === serialized) return;
    setStatus('Unsaved changes');
    const timer = setTimeout(() => { void flush().catch(() => {}); }, 400);
    return () => clearTimeout(timer);
  }, [id, ready, serialized]);
  useEffect(() => {
    if (!id) return;
    const warn = (event: BeforeUnloadEvent) => {
      if (saved.current !== latest.current) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [id]);
  return { flush, status, error };
}
