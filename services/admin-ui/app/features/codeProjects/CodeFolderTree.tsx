import { useEffect, useId, useState } from 'react';
import { ChevronDown, ChevronRight, Folder } from 'lucide-react';
import { getCodeFolders, type CodeDirectory } from './api';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/collapsible';
import { Field, FieldLabel } from '~/components/ui/field';
import { Spinner } from '~/components/ui/spinner';

interface Props {
  selected: string[];
  onChange: (folders: string[]) => void;
  disabled: boolean;
  lockedPath: string | null;
  path?: string;
}

export default function CodeFolderTree({ selected, onChange, disabled, lockedPath, path = '' }: Props) {
  const id = useId();
  const [open, setOpen] = useState(() => path === '' || [...selected, lockedPath ?? ''].some(folder => folder.startsWith(`${path}/`)));
  const [directory, setDirectory] = useState<CodeDirectory | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true); setError('');
    getCodeFolders(path, offset, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setDirectory(previous => ({ ...value, entries: offset === 0 ? value.entries
        : [...new Map([...(previous?.entries ?? []), ...value.entries].map(entry => [entry.path, entry])).values()] }));
      setLoading(false);
    }).catch(reason => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : 'Could not load folders.');
        setLoading(false);
      }
    });
    return () => controller.abort();
  }, [path, open, offset, retry]);

  return <Collapsible open={open} onOpenChange={value => { setOffset(0); setOpen(value); }}>
    {path ? <Field orientation="horizontal" className="gap-2" data-disabled={disabled || path === lockedPath}>
      <CollapsibleTrigger asChild><Button type="button" variant="ghost" size="icon" aria-label={`${open ? 'Collapse' : 'Expand'} ${path}`}>
        {open ? <ChevronDown /> : <ChevronRight />}
      </Button></CollapsibleTrigger>
      <Checkbox id={id} aria-label={`Select folder ${path}`} disabled={disabled || path === lockedPath}
        checked={selected.includes(path) || path === lockedPath}
        onCheckedChange={checked => onChange(checked === true ? [...selected, path] : selected.filter(folder => folder !== path))} />
      <FieldLabel htmlFor={id} className="min-w-0 break-all"><Folder className="size-4 shrink-0" />{path.split('/').at(-1)}</FieldLabel>
    </Field> : <p className="mb-2 text-sm font-medium">{directory?.root ?? 'Code root'}</p>}
    <CollapsibleContent className={path ? 'ml-4 border-l pl-3' : undefined}>
      {directory && <ul className="flex flex-col gap-1">{directory.entries.map(folder => <li key={folder.path}>
        <CodeFolderTree path={folder.path} selected={selected} onChange={onChange} disabled={disabled} lockedPath={lockedPath} />
      </li>)}</ul>}
      {loading && <p role="status" className="flex items-center gap-2 py-2 text-sm text-muted-foreground"><Spinner />Loading folders…</p>}
      {error && <Alert variant="destructive"><AlertDescription>{error}<Button type="button" variant="outline" size="sm" onClick={() => setRetry(value => value + 1)}>Retry folders</Button></AlertDescription></Alert>}
      {!loading && !error && directory?.entries.length === 0 && <p className="py-2 text-sm text-muted-foreground">{path ? 'No subfolders.' : 'No folders in the code root yet.'}</p>}
      {directory?.next_offset != null && !error && <Button type="button" variant="ghost" size="sm" disabled={loading}
        aria-label={`Load more folders in ${path || 'code root'}`} onClick={() => setOffset(directory.next_offset!)}>Load more folders</Button>}
    </CollapsibleContent>
  </Collapsible>;
}
