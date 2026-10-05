import { useEffect, useState } from "react";
import { getCollections, ragRequest, type RagCollection, type RagResults } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Textarea } from "~/components/ui/textarea";

const EACH = "each";

export default function RagSearchView() {
  const [query, setQuery] = useState("");
  const [collections, setCollections] = useState<RagCollection[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [mode, setMode] = useState("");
  const [top, setTop] = useState(10);
  const [result, setResult] = useState<RagResults | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getCollections(controller.signal).then((r) => { if (!controller.signal.aborted) setCollections(r.collections); }).catch(() => {});
    return () => controller.abort();
  }, []);
  async function run() {
    setBusy(true); setError(""); setResult(null);
    try { setResult(await ragRequest<RagResults>("search", "POST", { query, top, ...(selected.length ? { collections: selected } : {}), ...(mode ? { mode } : {}) })); }
    catch (e) { setError(e instanceof Error ? e.message : "Search failed"); } finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-6">
    <form className="flex flex-col gap-5 rounded-lg border p-5" onSubmit={(e) => { e.preventDefault(); run(); }}>
      <Field><FieldLabel htmlFor="rag-query">Search query</FieldLabel><Textarea id="rag-query" required maxLength={4000} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="How does the payments service handle retries?" /></Field>
      <Field><FieldLabel>Collections</FieldLabel><div className="flex flex-wrap gap-5">{collections.map((c) => <Field key={c.id} orientation="horizontal" className="w-auto"><Checkbox id={`search-collection-${c.id}`} checked={selected.includes(c.name)} onCheckedChange={(checked) => setSelected(checked === true ? [...selected, c.name] : selected.filter((n) => n !== c.name))} /><FieldLabel htmlFor={`search-collection-${c.id}`}>{c.name}</FieldLabel></Field>)}</div><FieldDescription>Select none to search every collection.</FieldDescription></Field>
      <div className="grid gap-5 sm:grid-cols-[2fr_100px]">
        <Field><FieldLabel htmlFor="rag-mode">Search type</FieldLabel><Select value={mode || EACH} onValueChange={(v) => setMode(v === EACH ? "" : v)}><SelectTrigger id="rag-mode"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value={EACH}>Each collection&apos;s mode</SelectItem><SelectItem value="full_text">Full text</SelectItem><SelectItem value="embedding">Embedding</SelectItem><SelectItem value="hybrid">Hybrid</SelectItem></SelectGroup></SelectContent></Select></Field>
        <Field><FieldLabel htmlFor="rag-top">Results</FieldLabel><Input id="rag-top" type="number" required min={1} max={30} value={top} onChange={(e) => setTop(Number(e.target.value))} /></Field></div>
      <div className="flex gap-2"><Button type="submit" disabled={busy || !query.trim()}>Search</Button></div>
      <p className="text-sm text-muted-foreground">Use Agents to ask questions with cited answers. Collections without embeddings fall back to keyword search.</p>
    </form>
    {busy && <p role="status" className="text-sm text-muted-foreground">Retrieving evidence…</p>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {result && <div className="flex flex-col gap-4"><p role="status" className="text-sm text-muted-foreground">{result.results.length} results · {result.collections.join(", ")}</p>
      {result.fallback.length > 0 && <p className="text-sm text-muted-foreground">Keyword search used for: {result.fallback.join(", ")}</p>}
      {result.results.map((hit) => <article key={hit.id} className="flex flex-col gap-3 border-b pb-5"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{hit.citation}</Badge><h3 className="break-all text-sm font-semibold">{hit.collection}/{hit.path}</h3><Badge variant="secondary">{hit.score.toFixed(3)}</Badge></div><p className="whitespace-pre-wrap text-sm leading-relaxed">{hit.text}</p><p className="text-xs text-muted-foreground">Indexed {new Date(hit.indexed_at).toLocaleString()} · SHA-256 {hit.content_hash.slice(0, 12)}</p></article>)}
      {result.results.length === 0 && <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">No matches. Check the collection selection and refresh collections before searching.</p>}
    </div>}
  </div>;
}
