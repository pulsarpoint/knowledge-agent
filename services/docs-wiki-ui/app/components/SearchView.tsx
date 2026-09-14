import { useEffect, useState } from "react";
import { Link } from "react-router";
import { search, type SearchHit } from "../lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Input } from "~/components/ui/input";

/** The API highlights matches with literal <mark> tags inside otherwise
 * plain text. Render ONLY those as elements; every other character —
 * including any other tag-looking text — stays escaped text. */
export function renderSnippet(snippet: string): React.ReactNode {
  const parts = snippet.split(/<mark>(.*?)<\/mark>/g);
  return parts.map((part, i) =>
    i % 2 === 1 ? <mark key={i}>{part}</mark> : <span key={i}>{part}</span>);
}

export default function SearchView({ app }: { app: string }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  useEffect(() => {
    if (!q.trim()) { setHits([]); return; }
    const handle = setTimeout(() => {
      search(app, q).then(setHits).catch(() => setHits([]));
    }, 150);
    return () => clearTimeout(handle);
  }, [app, q]);
  return (
    <div className="max-w-2xl">
      <Input
        type="search"
        role="searchbox"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search the documentation…"
        className="w-full"
      />
      <ul className="mt-4 flex flex-col gap-3">
        {hits.map((hit, i) => (
          <li key={`${hit.path}-${i}`}>
            <Card>
              <CardHeader>
                {/* break-words + min-w-0: doc titles/headings and search
                    snippets are arbitrary indexed content and can be one long
                    unbroken token — without both, these flex items (children
                    of CardHeader's flex column) refuse to shrink below their
                    content's min-content width and blow out the whole page
                    at narrow widths (same mechanism proven live in
                    GapsView's question card at 375px). */}
                <CardTitle className="min-w-0 break-words text-base font-medium">
                  <Link
                    to={`/${app}/doc/${hit.path}${hit.anchor ? `#${hit.anchor}` : ""}`}
                    className="hover:text-primary"
                  >
                    {hit.title}
                  </Link>
                </CardTitle>
                <CardDescription className="min-w-0 break-words">{hit.heading_path}</CardDescription>
              </CardHeader>
              <CardContent className="min-w-0 break-words pt-0 text-sm">
                {renderSnippet(hit.snippet)}
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}
