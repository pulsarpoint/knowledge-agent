import { useEffect, useState } from "react";
import { ChevronRight, CornerLeftUp, Folder } from "lucide-react";
import { addFolder, getFolders, normalizeFolder, type FolderListing } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";

const parent = (path: string) => path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : null;

/** Browse DATA_ROOT or type a folder path, then connect the existing folder to the collection. */
export default function RagFolderPicker({ open, collection, onOpenChange, onAdded }: {
  open: boolean; collection: { id: string; name: string }; onOpenChange: (open: boolean) => void; onAdded: () => void;
}) {
  return <Sheet open={open} onOpenChange={onOpenChange}><SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-xl">
    <SheetHeader><SheetTitle>Add folder to {collection.name}</SheetTitle>
      <SheetDescription>Choose a folder under DATA_ROOT or type its path, such as code/payments or jira. The folder must already exist.</SheetDescription></SheetHeader>
    {open && <Picker collectionId={collection.id} onAdded={onAdded} />}
  </SheetContent></Sheet>;
}

function Picker({ collectionId, onAdded }: { collectionId: string; onAdded: () => void }) {
  const [browsing, setBrowsing] = useState<string | null>(null);
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [value, setValue] = useState("");
  const [browseError, setBrowseError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setListing(null); setBrowseError("");
    getFolders(browsing, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setListing(result); })
      .catch((e) => { if (!controller.signal.aborted) setBrowseError(e instanceof Error ? e.message : "Could not list folders"); });
    return () => controller.abort();
  }, [browsing]);
  function browse(path: string | null) { setBrowsing(path); setValue(path ?? ""); }
  async function submit() {
    setBusy(true); setError("");
    try { await addFolder(collectionId, normalizeFolder(value)); onAdded(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not add the folder"); setBusy(false); }
  }
  return <form className="flex flex-col gap-4 p-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-muted-foreground">Browsing</span><code>{browsing ?? "DATA_ROOT"}</code>
        {browsing !== null && <Button type="button" variant="ghost" size="sm" onClick={() => browse(parent(browsing))}><CornerLeftUp data-icon="inline-start" />Up</Button>}
      </div>
      {browseError && <p className="text-sm text-destructive">{browseError}</p>}
      {!listing && !browseError && <p className="text-sm text-muted-foreground">Loading…</p>}
      <ul className="flex flex-col rounded-md border">
        {listing?.folders.map((folder) => <li key={folder.path}>
          <Button type="button" variant="ghost" className="w-full justify-start" onClick={() => browse(folder.path)}>
            <Folder data-icon="inline-start" />{folder.name}<ChevronRight className="ml-auto" /></Button></li>)}
        {listing && listing.folders.length === 0 && <li className="p-3 text-sm text-muted-foreground">No subfolders.</li>}
      </ul>
      {listing?.truncated && <p className="text-xs text-muted-foreground">Only the first 1,000 folders are shown; type the path instead.</p>}
    </div>
    <Field><FieldLabel htmlFor="folder-path">Folder path</FieldLabel>
      <Input id="folder-path" value={value} placeholder="code/payments" onChange={(e) => setValue(e.target.value)} />
      <FieldDescription>Relative to DATA_ROOT, such as jira or code/payments.</FieldDescription></Field>
    <Button className="self-start" type="submit" disabled={busy || normalizeFolder(value) === ""}>Connect folder</Button>
  </form>;
}
