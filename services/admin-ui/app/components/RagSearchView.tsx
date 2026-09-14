import { useState } from "react";
import { ragRequest, type RagResults } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Textarea } from "~/components/ui/textarea";

export default function RagSearchView() {
  const [query, setQuery] = useState("");
  const [folder, setFolder] = useState("*");
  const [mode, setMode] = useState("full_text");
  const [top, setTop] = useState(8);
  const [result, setResult] = useState<RagResults | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run() {
    setBusy(true); setError(""); setResult(null);
    try { setResult(await ragRequest<RagResults>("search", "POST", { query, folder, mode, top })); }
    catch (e) { setError(e instanceof Error ? e.message : "Search failed"); } finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-6">
    <form className="flex flex-col gap-5 rounded-lg border p-5" onSubmit={(e) => { e.preventDefault(); run(); }}>
      <Field><FieldLabel htmlFor="rag-query">Search query</FieldLabel><Textarea id="rag-query" required maxLength={4000} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="How does the payments service handle retries?" /></Field>
      <div className="grid gap-5 sm:grid-cols-[2fr_1fr_100px]"><Field><FieldLabel htmlFor="rag-scope">Folder scope</FieldLabel><Input id="rag-scope" required value={folder} onChange={(e) => setFolder(e.target.value)} /><FieldDescription>* searches all collections. documentation.payments selects one folder; documentation.payments.* includes its subfolders.</FieldDescription></Field>
        <Field><FieldLabel htmlFor="rag-mode">Search type</FieldLabel><Select value={mode} onValueChange={setMode}><SelectTrigger id="rag-mode"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="full_text">Full text</SelectItem><SelectItem value="embedding">Embedding</SelectItem><SelectItem value="hybrid">Hybrid</SelectItem></SelectGroup></SelectContent></Select></Field>
        <Field><FieldLabel htmlFor="rag-top">Results</FieldLabel><Input id="rag-top" type="number" required min={1} max={30} value={top} onChange={(e) => setTop(Number(e.target.value))} /></Field></div>
      <div className="flex gap-2"><Button type="submit" disabled={busy || !query.trim()}>Search</Button></div>
      <p className="text-sm text-muted-foreground">Use Agents to ask questions with cited answers. Hybrid uses full text when embeddings are disabled.</p>
    </form>
    {busy && <p role="status" className="text-sm text-muted-foreground">Retrieving evidence…</p>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {result && <div className="flex flex-col gap-4"><p role="status" className="text-sm text-muted-foreground">{result.results.length} results · {result.mode.replaceAll("_", " ")} · {result.folder}</p>
      {result.results.map((hit) => <article key={hit.id} className="flex flex-col gap-3 border-b pb-5"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{hit.citation}</Badge><h3 className="break-all text-sm font-semibold">{hit.path}</h3><Badge variant="secondary">{hit.kind === "folder" ? "Folder description" : "File chunk"}</Badge></div><p className="whitespace-pre-wrap text-sm leading-relaxed">{hit.text}</p><p className="text-xs text-muted-foreground">Indexed {new Date(hit.indexed_at).toLocaleString()} · SHA-256 {hit.content_hash.slice(0, 12)}</p></article>)}
      {result.results.length === 0 && <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">No matches. Check the folder scope and process source files before searching.</p>}
    </div>}
  </div>;
}
