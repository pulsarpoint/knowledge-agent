import { useState } from "react";
import { ApiError } from "../lib/api";
import { createCollection, DEFAULT_SETTINGS, saveCollectionSettings, type CollectionSettings, type RagCollection, type SearchMode } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean);
const NAME = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const validInterval = (minutes: number) => Number.isInteger(minutes) && minutes >= 1 && minutes <= 1440;

/** The check interval to send: never one the API rejects. While auto-sync is off the (disabled) field may hold
 * anything, so an invalid value falls back to the saved interval, or 5; while on, it is clamped to 1..1440. */
export function pollIntervalToSend(minutes: number, autoSync: boolean, saved: number): number {
  if (validInterval(minutes)) return minutes;
  const fallback = validInterval(saved) ? saved : 5;
  if (!autoSync || !Number.isFinite(minutes)) return fallback;
  return Math.min(1440, Math.max(1, Math.round(minutes)));
}

/** Creates a collection (no `collection`) or edits its settings. */
export default function RagCollectionForm({ open, collection, onOpenChange, onSaved, onFailed }: {
  open: boolean; collection?: RagCollection; onOpenChange: (open: boolean) => void;
  onSaved: (saved: { id: string }) => void; onFailed?: () => void;
}) {
  return <Sheet open={open} onOpenChange={onOpenChange}><SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-xl">
    <SheetHeader><SheetTitle>{collection ? `Edit ${collection.name}` : "New collection"}</SheetTitle>
      <SheetDescription>{collection
        ? "Search settings for this collection. Add and remove folders on the collection page."
        : "Name the collection and choose its search settings, then add folders to it."}</SheetDescription></SheetHeader>
    {open && <SettingsFields collection={collection} onSaved={onSaved} onFailed={onFailed} />}
  </SheetContent></Sheet>;
}

function SettingsFields({ collection, onSaved, onFailed }: { collection?: RagCollection; onSaved: (saved: { id: string }) => void; onFailed?: () => void }) {
  const initial: CollectionSettings = collection ?? DEFAULT_SETTINGS;
  const [name, setName] = useState("");
  const [description, setDescription] = useState(initial.description);
  const [searchMode, setSearchMode] = useState<SearchMode>(initial.search_mode);
  const [embeddings, setEmbeddings] = useState(initial.embeddings_enabled);
  const [include, setInclude] = useState(initial.include.join("\n"));
  const [exclude, setExclude] = useState(initial.exclude.join("\n"));
  const [chunkSize, setChunkSize] = useState(initial.chunk_size);
  const [chunkOverlap, setChunkOverlap] = useState(initial.chunk_overlap);
  const [autoSync, setAutoSync] = useState(initial.auto_sync);
  const [pollInterval, setPollInterval] = useState(initial.poll_interval_minutes);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const nameInvalid = !collection && (name.length > 60 || !NAME.test(name));
  async function submit() {
    setBusy(true); setError("");
    const value: CollectionSettings = { description, search_mode: searchMode, embeddings_enabled: embeddings,
      include: lines(include), exclude: lines(exclude), chunk_size: chunkSize, chunk_overlap: chunkOverlap,
      auto_sync: autoSync, poll_interval_minutes: pollIntervalToSend(pollInterval, autoSync, initial.poll_interval_minutes) };
    try {
      onSaved(collection
        ? await saveCollectionSettings(collection.id, { ...value, revision: collection.revision })
        : await createCollection({ name, ...value }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the collection"); setBusy(false);
      if (e instanceof ApiError && e.status === 409) onFailed?.();
    }
  }
  return <form className="flex flex-col gap-5 p-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <FieldGroup>
      {!collection && <Field><FieldLabel htmlFor="collection-name">Name</FieldLabel>
        <Input id="collection-name" required maxLength={60} value={name} aria-invalid={name !== "" && nameInvalid} onChange={(e) => setName(e.target.value.trim())} />
        {name !== "" && nameInvalid
          ? <FieldDescription className="text-destructive">Use lowercase letters, digits and single hyphens, up to 60 characters.</FieldDescription>
          : <FieldDescription>For example payments-code. The name cannot be changed later.</FieldDescription>}</Field>}
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
      <Field orientation="horizontal"><Switch id="collection-auto-sync" checked={autoSync} onCheckedChange={setAutoSync} /><FieldLabel htmlFor="collection-auto-sync">Auto-sync</FieldLabel></Field>
      <Field><FieldLabel htmlFor="collection-poll-interval">Check every (minutes)</FieldLabel>
        <Input id="collection-poll-interval" type="number" required min={1} max={1440} disabled={!autoSync} value={pollInterval} onChange={(e) => setPollInterval(Number(e.target.value))} />
        <FieldDescription>RAG checks the folders at this interval and syncs changes once they have been quiet for 2 minutes.</FieldDescription></Field>
    </FieldGroup>
    <Button className="self-start" type="submit" disabled={busy || nameInvalid}>{collection ? "Save collection" : "Create collection"}</Button>
  </form>;
}
