import { useState } from "react";
import { ApiError } from "../lib/api";
import { saveCollectionSettings, type RagCollection, type SearchMode } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean);

export default function RagCollectionForm({ open, collection, onOpenChange, onSaved, onFailed }: {
  open: boolean; collection: RagCollection; onOpenChange: (open: boolean) => void; onSaved: () => void; onFailed?: () => void;
}) {
  return <Sheet open={open} onOpenChange={onOpenChange}><SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-xl">
    <SheetHeader><SheetTitle>Edit {collection.name}</SheetTitle>
      <SheetDescription>Search settings for this collection. Its paths are added and removed by their sources.</SheetDescription></SheetHeader>
    {open && <SettingsFields collection={collection} onSaved={onSaved} onFailed={onFailed} />}
  </SheetContent></Sheet>;
}

function SettingsFields({ collection, onSaved, onFailed }: { collection: RagCollection; onSaved: () => void; onFailed?: () => void }) {
  const [description, setDescription] = useState(collection.description);
  const [searchMode, setSearchMode] = useState<SearchMode>(collection.search_mode);
  const [embeddings, setEmbeddings] = useState(collection.embeddings_enabled);
  const [include, setInclude] = useState(collection.include.join("\n"));
  const [exclude, setExclude] = useState(collection.exclude.join("\n"));
  const [chunkSize, setChunkSize] = useState(collection.chunk_size);
  const [chunkOverlap, setChunkOverlap] = useState(collection.chunk_overlap);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    setBusy(true); setError("");
    try {
      await saveCollectionSettings(collection.id, {
        description, search_mode: searchMode, embeddings_enabled: embeddings, include: lines(include), exclude: lines(exclude),
        chunk_size: chunkSize, chunk_overlap: chunkOverlap, revision: collection.revision,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the collection"); setBusy(false);
      if (e instanceof ApiError && e.status === 409) onFailed?.();
    }
  }
  return <form className="flex flex-col gap-5 p-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <FieldGroup>
      <Field><FieldLabel htmlFor="collection-description">Description</FieldLabel><Textarea id="collection-description" value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      <Field><FieldLabel htmlFor="collection-mode">Search mode</FieldLabel>
        <Select value={searchMode} onValueChange={(v) => setSearchMode(v as SearchMode)}><SelectTrigger id="collection-mode"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value="full_text">Keyword</SelectItem><SelectItem value="embedding">Semantic</SelectItem><SelectItem value="hybrid">Hybrid</SelectItem></SelectGroup></SelectContent></Select></Field>
      <Field orientation="horizontal"><Switch id="collection-embeddings" checked={embeddings} onCheckedChange={setEmbeddings} /><FieldLabel htmlFor="collection-embeddings">Embeddings</FieldLabel></Field>
      <Field><FieldLabel htmlFor="collection-include">Include patterns</FieldLabel><Textarea id="collection-include" value={include} onChange={(e) => setInclude(e.target.value)} />
        <FieldDescription>One glob pattern per line, matched against each file's path inside its folder.</FieldDescription></Field>
      <Field><FieldLabel htmlFor="collection-exclude">Exclude patterns</FieldLabel><Textarea id="collection-exclude" value={exclude} onChange={(e) => setExclude(e.target.value)} /></Field>
      <div className="grid gap-5 sm:grid-cols-2">
        <Field><FieldLabel htmlFor="collection-chunk-size">Chunk size</FieldLabel><Input id="collection-chunk-size" type="number" required min={200} max={12000} value={chunkSize} onChange={(e) => setChunkSize(Number(e.target.value))} /></Field>
        <Field><FieldLabel htmlFor="collection-chunk-overlap">Chunk overlap</FieldLabel><Input id="collection-chunk-overlap" type="number" required min={0} max={2000} value={chunkOverlap} onChange={(e) => setChunkOverlap(Number(e.target.value))} /></Field>
      </div>
    </FieldGroup>
    <Button className="self-start" type="submit" disabled={busy}>Save collection</Button>
  </form>;
}
