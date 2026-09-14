import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { ApiError, getDoc, type DocData } from "../lib/api";
import Markdown from "./Markdown";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "~/components/ui/collapsible";
import { useIsDesktop } from "~/hooks/use-desktop";

export default function DocView({ app, path }: { app: string; path: string }) {
  const [doc, setDoc] = useState<DocData | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Called unconditionally (Rules of Hooks) even though it's only consumed
  // below the early returns.
  const isDesktop = useIsDesktop();
  useEffect(() => {
    setDoc(null); setError(null);
    getDoc(app, path).then(setDoc).catch((e) =>
      setError(e instanceof ApiError ? e.message : "failed to load document"));
  }, [app, path]);

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!doc) return <p className="text-sm text-muted-foreground">Loading…</p>;
  const updated = typeof doc.front_matter.updated === "string" ? doc.front_matter.updated : null;
  // Build metadata straight from front matter: the `updated` date and the
  // per-service commits (analyzed_commits: service -> full sha) the doc
  // was built against. Short shas only — this is orientation, not a link.
  const rawCommits = doc.front_matter.analyzed_commits;
  const commits = rawCommits && typeof rawCommits === "object" && !Array.isArray(rawCommits)
    ? Object.entries(rawCommits as Record<string, unknown>)
        .filter((e): e is [string, string] => typeof e[1] === "string" && e[1].length > 0)
    : [];
  // Single shared <ul> JSX value, reused by whichever branch below actually
  // mounts — never both at once, so there is still only ever one copy of
  // these anchor links in the DOM (see docView.test.tsx and the Task 3
  // report for why a CSS-only duplicate would make the existing unscoped
  // `getByRole("link", ...)` query ambiguous: jsdom applies no stylesheet,
  // so "hidden"/"lg:block" have zero rendering effect in tests).
  const headingList = (
    <ul className="flex flex-col gap-1">
      {doc.headings.map((h) => (
        <li key={h.anchor} style={{ marginLeft: (h.level - 2) * 12 }}>
          <a href={`#${h.anchor}`} className="text-muted-foreground hover:text-foreground">{h.text}</a>
        </li>
      ))}
    </ul>
  );
  return (
    <div className="flex flex-col gap-6 lg:flex-row lg:gap-8">
      <article className="prose order-2 max-w-3xl min-w-0 flex-1 lg:order-1">
        {(updated || commits.length > 0) && (
          <div className="not-prose mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {updated && <span>Updated {updated}</span>}
            {commits.length > 0 && (
              <span className="flex flex-wrap gap-x-3 gap-y-1">
                <span>built from:</span>
                {commits.map(([service, sha]) => (
                  <span key={service} className="font-mono">{service} @ {sha.slice(0, 7)}</span>
                ))}
              </span>
            )}
          </div>
        )}
        <Markdown content={doc.markdown} />
      </article>
      {doc.headings.length > 0 && (
        <aside className="order-1 w-full text-sm lg:order-2 lg:w-56 lg:shrink-0">
          {isDesktop ? (
            // At `lg`+: plain static label + list, no collapsible chrome —
            // reproduces the original desktop-only aside exactly.
            <>
              <p className="mb-2 font-semibold text-foreground">On this page</p>
              {headingList}
            </>
          ) : (
            // Below `lg`: a real branch (not a CSS-hidden trigger sharing
            // Radix state with the desktop rendering) — a single shared
            // Collapsible instance whose only trigger was `lg:hidden` left
            // the TOC stranded closed-with-no-control if a user collapsed it
            // on a narrow viewport and then widened past `lg` (tablet
            // rotation, split-screen, window resize) without a full remount,
            // since Radix's own open state doesn't reset on its own.
            // Branching on `useIsDesktop()` means crossing the breakpoint
            // swaps this element for the `isDesktop` branch above, which
            // unmounts this Collapsible entirely — so the next time this
            // branch mounts again (narrowing back below `lg`), it always
            // starts fresh at `defaultOpen`, never stuck closed.
            <Collapsible defaultOpen>
              <CollapsibleTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  className="group mb-2 w-full justify-between"
                >
                  On this page
                  <ChevronDown
                    data-icon="inline-end"
                    className="transition-transform group-data-[state=open]:rotate-180"
                  />
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent>{headingList}</CollapsibleContent>
            </Collapsible>
          )}
        </aside>
      )}
    </div>
  );
}
