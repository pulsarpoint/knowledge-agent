import { useState } from "react";
import { DEFAULT_COLLECTION, saveCollection, type CollectionInput, type RagCollection, type SearchMode } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

const AREAS = ["code", "code-index", "rag-data", "generated-documents", "docs"];
const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean);

export default function RagCollectionForm({ open, collection, onOpenChange, onSaved }: {
  open: boolean; collection?: RagCollection; onOpenChange: (open: boolean) => void; onSaved: () => void;
}) {
  return <Sheet open={open} onOpenChange={onOpenChange}><SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-xl">
    <SheetHeader><SheetTitle>{collection ? "Edit collection" : "New collection"}</SheetTitle>
      <SheetDescription>A collection maps a folder in the data root to one searchable unit.</SheetDescription></SheetHeader>
    {open && <CollectionFields collection={collection} onSaved={onSaved} />}
  </SheetContent></Sheet>;
}

function CollectionFields({ collection, onSaved }: { collection?: RagCollection; onSaved: () => void }) {
  const initial = collection ?? { ...DEFAULT_COLLECTION };
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [sourcePath, setSourcePath] = useState(initial.source_path);
  const [searchMode, setSearchMode] = useState<SearchMode>(initial.search_mode);
  const [embeddings, setEmbeddings] = useState(initial.embeddings_enabled);
  const [include, setInclude] = useState(initial.include.join("\n"));
  const [exclude, setExclude] = useState(initial.exclude.join("\n"));
  const [chunkSize, setChunkSize] = useState(initial.chunk_size);
  const [chunkOverlap, setChunkOverlap] = useState(initial.chunk_overlap);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    setBusy(true); setError("");
    const value: CollectionInput = {
      name: name.trim(), description, source_path: sourcePath.trim(), search_mode: searchMode, embeddings_enabled: embeddings,
      include: lines(include), exclude: lines(exclude), chunk_size: chunkSize, chunk_overlap: chunkOverlap,
      ...(collection ? { revision: collection.revision } : {}),
    };
    try { await saveCollection(value, collection?.id); onSaved(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save the collection"); setBusy(false); }
  }
  return <form className="flex flex-col gap-5 p-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <FieldGroup>
      <Field><FieldLabel htmlFor="collection-name">Name</FieldLabel><Input id="collection-name" required value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field><FieldLabel htmlFor="collection-description">Description</FieldLabel><Textarea id="collection-description" value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      <Field><FieldLabel htmlFor="collection-folder">Folder</FieldLabel><Input id="collection-folder" required value={sourcePath} onChange={(e) => setSourcePath(e.target.value)} />
        <FieldDescription>Inside DATA_ROOT: code/…, code-index/…, rag-data/…, generated-documents, docs</FieldDescription>
        <div className="flex flex-wrap gap-2">{AREAS.map((area) => <Button key={area} type="button" variant="outline" size="sm" onClick={() => setSourcePath(area)}>{area}</Button>)}</div></Field>
      <Field><FieldLabel htmlFor="collection-mode">Search mode</FieldLabel>
        <Select value={searchMode} onValueChange={(v) => setSearchMode(v as SearchMode)}><SelectTrigger id="collection-mode"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value="full_text">Keyword</SelectItem><SelectItem value="embedding">Semantic</SelectItem><SelectItem value="hybrid">Hybrid</SelectItem></SelectGroup></SelectContent></Select></Field>
      <Field orientation="horizontal"><Switch id="collection-embeddings" checked={embeddings} onCheckedChange={setEmbeddings} /><FieldLabel htmlFor="collection-embeddings">Embeddings</FieldLabel></Field>
      <Field><FieldLabel htmlFor="collection-include">Include patterns</FieldLabel><Textarea id="collection-include" value={include} onChange={(e) => setInclude(e.target.value)} /><FieldDescription>One glob pattern per line.</FieldDescription></Field>
      <Field><FieldLabel htmlFor="collection-exclude">Exclude patterns</FieldLabel><Textarea id="collection-exclude" value={exclude} onChange={(e) => setExclude(e.target.value)} /></Field>
      <div className="grid gap-5 sm:grid-cols-2">
        <Field><FieldLabel htmlFor="collection-chunk-size">Chunk size</FieldLabel><Input id="collection-chunk-size" type="number" required min={200} max={12000} value={chunkSize} onChange={(e) => setChunkSize(Number(e.target.value))} /></Field>
        <Field><FieldLabel htmlFor="collection-chunk-overlap">Chunk overlap</FieldLabel><Input id="collection-chunk-overlap" type="number" required min={0} max={2000} value={chunkOverlap} onChange={(e) => setChunkOverlap(Number(e.target.value))} /></Field>
      </div>
    </FieldGroup>
    <Button className="self-start" type="submit" disabled={busy}>{collection ? "Save collection" : "Create collection"}</Button>
  </form>;
}
