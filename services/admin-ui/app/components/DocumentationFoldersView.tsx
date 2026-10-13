import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { getDocumentationFolder, getDocumentationFolders, type DocumentationFolders, type FolderDocument } from '~/lib/documents';
import { getCollectionNames, type CollectionSummary } from '~/lib/rag';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

const folderLink = (folder: string) => `/documentation/folders/${folder.split('/').map(encodeURIComponent).join('/')}`;
const message = (e: unknown, fallback: string) => e instanceof Error ? e.message : fallback;
const when = (value: string | null) => value ? new Date(value).toLocaleString() : '—';

/** Collections whose folders include `source`: the folder itself, or a parent folder ("through" it). */
function including(collections: Pick<CollectionSummary, 'id' | 'name' | 'paths'>[], source: string) {
  return collections.flatMap(c => c.paths.filter(p => p.source_path === source || source.startsWith(p.source_path + '/'))
    .map(p => ({ id: c.id, name: c.name, through: p.source_path === source ? null : p.source_path })));
}

export default function DocumentationFoldersView() {
  const [data, setData] = useState<DocumentationFolders | null>(null);
  const [collections, setCollections] = useState<CollectionSummary[] | null>(null);
  const [ragError, setRagError] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    getDocumentationFolders(controller.signal).then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(e => { if (!controller.signal.aborted) setError(message(e, 'Could not load documentation folders')); });
    // Read-only RAG state: which collections include each folder. Indexing happens in RAG → Collections.
    getCollectionNames(controller.signal).then(result => { if (!controller.signal.aborted) setCollections(result.collections); })
      .catch(e => { if (!controller.signal.aborted) setRagError(message(e, 'RAG is unavailable')); });
    return () => controller.abort();
  }, []);
  return <div className="flex flex-col gap-5">
    <div><h1 className="text-2xl font-semibold">Documentation folders</h1><p className="text-muted-foreground">Folders in the generated documents folder{data?.generated_folder ? ` (${data.generated_folder})` : ''}. To make a folder searchable, create a collection with it in RAG → Collections.</p></div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {ragError && <Alert><AlertDescription>{`RAG collections unavailable: ${ragError}`}</AlertDescription></Alert>}
    {data === null ? (!error && <Skeleton className="h-40" />) : data.folders.length === 0
      ? <p>No documentation folders yet. Write a code project document into a folder with an agent from Documents → New.</p>
      : <Table><TableHeader><TableRow><TableHead>Folder</TableHead><TableHead>Documents</TableHead><TableHead>Code projects</TableHead><TableHead>Last generated</TableHead><TableHead>RAG collections</TableHead><TableHead>Index</TableHead></TableRow></TableHeader><TableBody>
        {data.folders.map(row => {
          const found = row.source_path && collections ? including(collections, row.source_path) : [];
          return <TableRow key={row.folder}>
            <TableCell><Link className="underline" to={folderLink(row.folder)}>{row.folder}</Link></TableCell>
            <TableCell>{row.document_count}</TableCell>
            <TableCell>{row.code_projects.join(', ') || '—'}</TableCell>
            <TableCell>{when(row.last_generated_at)}</TableCell>
            <TableCell>{collections === null ? (ragError ? '—' : 'Loading…') : found.length === 0 ? <Badge variant="outline">Not indexed</Badge>
              : <ul className="flex flex-col gap-1">{found.map(c => <li key={`${c.id}:${c.through ?? ''}`} className="text-sm">
                  <Link className="underline" to={`/rag/collections/${c.id}`}>{c.name}</Link>
                  {c.through && <span className="ml-2 text-xs text-muted-foreground">{`through ${c.through}`}</span>}</li>)}</ul>}</TableCell>
            <TableCell>{row.source_path ? <Link className="underline" aria-label={`Index ${row.folder} in RAG → Collections`}
              to={`/rag/collections?folder=${encodeURIComponent(row.source_path)}`}>Index in RAG → Collections</Link> : '—'}</TableCell>
          </TableRow>;
        })}
      </TableBody></Table>}
  </div>;
}

export function DocumentationFolderView({ folder }: { folder: string }) {
  const [rows, setRows] = useState<FolderDocument[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setRows(null); setError('');
    getDocumentationFolder(folder, controller.signal).then(result => { if (!controller.signal.aborted) setRows(result.documents); })
      .catch(e => { if (!controller.signal.aborted) setError(message(e, 'Could not open the folder')); });
    return () => controller.abort();
  }, [folder]);
  return <div className="flex flex-col gap-5"><Link className="underline" to="/documentation/folders">Documentation folders</Link><h1 className="text-2xl font-semibold">{folder}</h1>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {rows === null ? (!error && <Skeleton className="h-40" />) : <Table><TableHeader><TableRow><TableHead>Document</TableHead><TableHead>Code project</TableHead><TableHead>Last generated</TableHead><TableHead>Version</TableHead></TableRow></TableHeader><TableBody>
      {rows.map(d => <TableRow key={d.id}><TableCell><Link className="underline" to={`/documents/${d.id}`}>{d.title}</Link></TableCell><TableCell>{d.code_projects.join(', ') || '—'}</TableCell><TableCell>{when(d.last_generated_at)}</TableCell><TableCell>v{d.version}</TableCell></TableRow>)}
    </TableBody></Table>}
  </div>;
}
