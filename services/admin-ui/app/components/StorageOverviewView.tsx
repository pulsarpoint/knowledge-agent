import { Fragment, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { ArrowRight, Database, FileText, Folder, FolderGit2, RefreshCw } from 'lucide-react';
import { getStorage, getStorageDirectory, getStorageFile, type StorageArea, type StorageDirectory, type StorageLocation } from '~/lib/storage';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from '~/components/ui/breadcrumb';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '~/components/ui/sheet';
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from '~/components/ui/empty';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

const areas = {
  code: { name: 'Source code', icon: FolderGit2, purpose: 'Mapped project folders and Git clones. Agents read the folders selected in their configuration.', layout: 'Project → one or more folders. Git pulls update the configured clone folder.', link: '/code-projects', action: 'Manage code projects' },
  rag: { name: 'RAG source files', icon: Folder, purpose: 'Files uploaded for search, including Jira ticket exports. Processing creates searchable text and embeddings in PostgreSQL.', layout: 'Collection folder → subfolders → source files. Process a folder from RAG Files & folders.', link: '/rag/files', action: 'Manage RAG files' },
  documents: { name: 'Document exports', icon: FileText, purpose: 'Markdown and D2 files exported from managed documents. PostgreSQL holds the original documents and version history.', layout: 'Document slug → v1, v2, … → README.md and architecture-N.d2. Generation exports automatically; manual documents have an Export action.', link: '/documents', action: 'Manage documents' },
  bootstrap: { name: 'Bootstrap docs', icon: Folder, purpose: 'The original startup directory, seeded with example Markdown. This is not the current RAG source folder or the generated-document destination.', layout: 'Legacy startup files. Add new knowledge through RAG files or managed Documents.', link: null, action: null },
} as const;
const message = (reason: unknown) => reason instanceof Error ? reason.message : 'Could not load storage.';
const browseUrl = (area: StorageArea, path = '', offset = 0) => `/storage?${new URLSearchParams({ area, path, offset: String(offset) })}#files`;
const fileSize = (size: number | null) => size === null ? '—' : size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;

export default function StorageOverviewView() {
  const [params] = useSearchParams();
  const requestedArea = params.get('area');
  const area: StorageArea = requestedArea && Object.hasOwn(areas, requestedArea) ? requestedArea as StorageArea : 'code';
  const path = params.get('path') ?? '';
  const rawOffset = Number(params.get('offset') ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const [locations, setLocations] = useState<StorageLocation[] | null>(null);
  const [overviewError, setOverviewError] = useState('');
  const [directory, setDirectory] = useState<StorageDirectory | null>(null);
  const [directoryError, setDirectoryError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [preview, setPreview] = useState<{ area: StorageArea; path: string } | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState('');
  const selected = locations?.find(location => location.id === area);

  useEffect(() => {
    const controller = new AbortController();
    setOverviewError('');
    getStorage(controller.signal).then(value => { if (!controller.signal.aborted) setLocations(value.locations); })
      .catch(reason => { if (!controller.signal.aborted) setOverviewError(message(reason)); });
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    const controller = new AbortController();
    setDirectory(null); setDirectoryError(''); setPreview(null);
    getStorageDirectory(area, path, offset, controller.signal).then(value => { if (!controller.signal.aborted) setDirectory(value); })
      .catch(reason => { if (!controller.signal.aborted) setDirectoryError(message(reason)); });
    return () => controller.abort();
  }, [area, path, offset, refresh]);
  useEffect(() => {
    const controller = new AbortController();
    setText(null); setPreviewError('');
    if (preview) getStorageFile(preview.area, preview.path, controller.signal).then(value => { if (!controller.signal.aborted) setText(value.text); })
      .catch(reason => { if (!controller.signal.aborted) setPreviewError(message(reason)); });
    return () => controller.abort();
  }, [preview]);

  return <>
    <Card>
      <CardHeader><CardTitle>Where information lives</CardTitle><CardDescription>Host folders are mounted into Docker. Browse the current files below, or open the corresponding management page.</CardDescription></CardHeader>
      <CardContent>
        {overviewError && <Alert variant="destructive"><AlertDescription>{overviewError}</AlertDescription></Alert>}
        {!locations && !overviewError && <Skeleton className="h-60 w-full" />}
        {locations && <Table><TableHeader><TableRow><TableHead>Information</TableHead><TableHead>Location</TableHead><TableHead>Actions</TableHead></TableRow></TableHeader><TableBody>
          {locations.map(location => {
            const info = areas[location.id];
            return <TableRow key={location.id} data-state={area === location.id ? 'selected' : undefined}>
              <TableCell className="min-w-52 max-w-sm align-top"><div className="flex items-center gap-2 font-medium"><info.icon className="size-4 shrink-0" />{info.name}</div><p className="mt-2 whitespace-normal text-sm text-muted-foreground">{info.purpose}</p></TableCell>
              <TableCell className="min-w-56 max-w-sm align-top"><dl className="flex flex-col gap-1 text-sm">
                <dt className="text-muted-foreground">Host · {location.host_variable}</dt><dd><code className="whitespace-normal break-all">{location.host_path ?? 'Not supplied by deployment'}</code></dd>
                {location.host_path_relative && <dd className="whitespace-normal text-xs text-muted-foreground">Relative to the Compose file directory on the host.</dd>}
                <dt className="mt-2 text-muted-foreground">Inside Docker</dt><dd><code className="whitespace-normal break-all">{location.container_path ?? 'Not configured'}</code></dd>
              </dl></TableCell>
              <TableCell className="align-top"><div className="flex flex-col items-start gap-2"><Badge variant={location.status === 'available' ? 'secondary' : 'outline'}>{location.status === 'available' ? 'Available' : location.status === 'unavailable' ? 'Folder unavailable' : 'Not configured'}</Badge>
                <Button asChild variant="outline" size="sm"><Link to={browseUrl(location.id)} aria-label={`Browse ${info.name}`}>Browse files</Link></Button>
                {info.link && <Button asChild variant="link" size="sm" className="px-0"><Link to={info.link}>{info.action}<ArrowRight data-icon="inline-end" /></Link></Button>}
              </div></TableCell>
            </TableRow>;
          })}
        </TableBody></Table>}
      </CardContent>
    </Card>

    <Card id="files" className="scroll-mt-6">
      <CardHeader><div className="flex items-center justify-between gap-3"><CardTitle>{areas[area].name} · file browser</CardTitle><Button variant="outline" size="sm" onClick={() => setRefresh(value => value + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button></div><CardDescription>{areas[area].layout}</CardDescription></CardHeader>
      <CardContent className="flex min-w-0 flex-col gap-4">
        <Breadcrumb><BreadcrumbList><BreadcrumbItem>{path ? <BreadcrumbLink asChild><Link to={browseUrl(area)}>{selected?.container_path ?? areas[area].name}</Link></BreadcrumbLink> : <BreadcrumbPage>{selected?.container_path ?? areas[area].name}</BreadcrumbPage>}</BreadcrumbItem>
          {path.split('/').filter(Boolean).map((part, index, parts) => <Fragment key={index}><BreadcrumbSeparator /><BreadcrumbItem>{index === parts.length - 1 ? <BreadcrumbPage>{part}</BreadcrumbPage> : <BreadcrumbLink asChild><Link to={browseUrl(area, parts.slice(0, index + 1).join('/'))}>{part}</Link></BreadcrumbLink>}</BreadcrumbItem></Fragment>)}
        </BreadcrumbList></Breadcrumb>
        {directoryError ? <Alert variant="destructive"><AlertDescription>{directoryError}</AlertDescription></Alert> : !directory ? <Skeleton className="h-40 w-full" /> : directory.total === 0 ?
          <Empty><EmptyHeader><EmptyTitle>This folder is empty</EmptyTitle><EmptyDescription>{area === 'documents' ? 'Generate documentation or export a saved document to create its version folder here.' : 'Use the management page to add content to this location.'}</EmptyDescription></EmptyHeader></Empty> :
          <Table><TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Type</TableHead><TableHead>Size</TableHead><TableHead>Modified</TableHead></TableRow></TableHeader><TableBody>
            {directory.entries.map(entry => <TableRow key={entry.path}><TableCell className="max-w-lg">
              {entry.kind === 'folder' ? <Button asChild variant="link" className="h-auto max-w-full justify-start whitespace-normal px-0 text-left"><Link to={browseUrl(area, entry.path)}><Folder data-icon="inline-start" /><span className="break-all">{entry.name}</span></Link></Button> : entry.kind === 'file' ?
                <Button variant="link" className="h-auto max-w-full justify-start whitespace-normal px-0 text-left" onClick={() => setPreview({ area, path: entry.path })}><FileText data-icon="inline-start" /><span className="break-all">{entry.name}</span></Button> : <span className="whitespace-normal break-all">{entry.name}</span>}
              </TableCell><TableCell>{entry.kind === 'symlink' ? 'Symbolic link · not followed' : entry.kind === 'folder' ? 'Folder' : entry.kind === 'file' ? 'File' : 'Special file · not opened'}</TableCell><TableCell>{fileSize(entry.size)}</TableCell><TableCell>{new Date(entry.modified_at).toLocaleString()}</TableCell></TableRow>)}
          </TableBody></Table>}
      </CardContent>
      <CardFooter className="flex flex-wrap justify-between gap-3"><p className="text-sm text-muted-foreground">Read-only browser · text preview up to 512 KB{directory ? ` · ${directory.total} entries in this folder` : ''}</p><div className="flex gap-2">
        {offset > 0 && <Button asChild variant="outline" size="sm"><Link to={browseUrl(area, path, Math.max(0, offset - 200))}>Previous files</Link></Button>}
        {directory?.next_offset != null && <Button asChild variant="outline" size="sm"><Link to={browseUrl(area, path, directory.next_offset)}>More files</Link></Button>}
      </div></CardFooter>
    </Card>

    <Card>
      <CardHeader><CardTitle>How files become knowledge</CardTitle><CardDescription>Each step has a specific destination.</CardDescription></CardHeader>
      <CardContent className="flex flex-col gap-5">
        <ol className="grid gap-5 text-sm md:grid-cols-3">
          <li><p className="font-medium">1. Source code and server evidence</p><p className="mt-1 text-muted-foreground">Code projects map folders under the code root. Documentation generation can analyze code and inspect selected servers.</p></li>
          <li><p className="font-medium">2. Managed documents and exports</p><p className="mt-1 text-muted-foreground">Documents are saved with version history in PostgreSQL. Markdown and D2 exports are written into the document export folder.</p></li>
          <li><p className="font-medium">3. RAG source files → search</p><p className="mt-1 text-muted-foreground">Upload selected files to RAG, then process their folder. Extracted text, hashes and embeddings are stored in PostgreSQL for search.</p></li>
        </ol>
        <Alert><AlertDescription>Generated documents are not automatically published to RAG. Upload their Markdown to a RAG folder and process it when you want it included in search. Jira exports go directly into the RAG folder you select and also require processing.</AlertDescription></Alert>
        <div className="flex items-start gap-3 text-sm"><Database className="mt-0.5 size-4 shrink-0" /><div><p className="font-medium">Some information lives in the database, not in a content folder</p><p className="mt-1 text-muted-foreground">PostgreSQL stores managed documents and versions, service and environment relationships, agent configuration, sessions, jobs, encrypted credentials, and RAG indexes. These records are managed through their administration pages. Docker also keeps separate volumes for login data, traces, and reader activity.</p></div></div>
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2"><Button asChild variant="outline"><Link to="/documentation/generations">Generate documentation</Link></Button><Button asChild variant="outline"><Link to="/rag/files">Upload and process RAG files</Link></Button></CardFooter>
    </Card>

    <Sheet open={preview !== null} onOpenChange={open => { if (!open) setPreview(null); }}><SheetContent className="flex w-full flex-col gap-4 sm:max-w-4xl"><SheetHeader><SheetTitle className="break-all">{preview?.path}</SheetTitle><SheetDescription>Read-only file preview. Edit content through its management page or host folder.</SheetDescription></SheetHeader>
      {previewError ? <Alert variant="destructive"><AlertDescription>{previewError}</AlertDescription></Alert> : text === null ? <Skeleton className="h-40 w-full" /> : <pre className="max-h-[65vh] overflow-auto rounded-md border bg-muted p-4 text-sm">{text || '(Empty file)'}</pre>}
    </SheetContent></Sheet>
  </>;
}
