import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { getGaps, type CodeCitation, type GapsData } from "../lib/api";
import Markdown from "./Markdown";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "~/components/ui/collapsible";
import { Empty, EmptyDescription } from "~/components/ui/empty";

type Gap = GapsData["gaps"][number];
type NearMiss = Gap["near_misses"][number];
type CodeAnswer = { answer: string; citations: CodeCitation[]; notFound: boolean; timestamp: string };

// The server only validates that each gaps-log line is a JSON object — field
// presence/types aren't guaranteed. Normalize defensively so a malformed
// entry (missing/wrong-typed near_misses, question, timestamp, or
// code_answer) renders as blank/empty rather than throwing.
function normalizeGap(gap: Gap): {
  question: string; timestamp: string; nearMisses: NearMiss[]; codeAnswer: CodeAnswer | null;
} {
  const g = gap as unknown as Record<string, unknown>;
  const rawNearMisses = Array.isArray(g.near_misses) ? g.near_misses : [];
  return {
    question: typeof g.question === "string" ? g.question : "",
    timestamp: typeof g.timestamp === "string" ? g.timestamp : "",
    nearMisses: rawNearMisses.map((m) => {
      const mm = (m ?? {}) as Record<string, unknown>;
      return {
        path: typeof mm.path === "string" ? mm.path : "",
        heading_path: typeof mm.heading_path === "string" ? mm.heading_path : "",
      };
    }),
    codeAnswer: normalizeCodeAnswer(g.code_answer),
  };
}

// load_gaps builds the code_answer object itself from a separate
// "code-answer" JSONL entry's fields (see gaps.py) — those fields come
// from `entry.get(...)`, so a truncated/hand-edited line can still leave
// any of them missing or wrong-typed. null (not folded in) rather than a
// GapsView crash when that happens.
function normalizeCodeAnswer(raw: unknown): CodeAnswer | null {
  if (raw === null || typeof raw !== "object") return null;
  const c = raw as Record<string, unknown>;
  const rawCitations = Array.isArray(c.citations) ? c.citations : [];
  return {
    answer: typeof c.answer === "string" ? c.answer : "",
    notFound: c.not_found === true,
    timestamp: typeof c.timestamp === "string" ? c.timestamp : "",
    citations: rawCitations.map((ci) => {
      const cc = (ci ?? {}) as Record<string, unknown>;
      return {
        file: typeof cc.file === "string" ? cc.file : "",
        lines: typeof cc.lines === "string" ? cc.lines : "",
      };
    }),
  };
}

export default function GapsView({ app }: { app: string }) {
  const [data, setData] = useState<GapsData | null>(null);
  useEffect(() => { getGaps(app).then(setData).catch(() => setData({ gaps: [], skipped: 0 })); }, [app]);
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;
  return (
    <div className="max-w-2xl flex flex-col gap-4">
      <h1 className="text-xl font-bold">Documentation gaps</h1>
      {data.skipped > 0 && (
        <p className="text-sm text-muted-foreground">{data.skipped} malformed entries skipped.</p>
      )}
      {data.gaps.length === 0 && (
        <Empty>
          <EmptyDescription>No gaps recorded — Ask has answered everything so far.</EmptyDescription>
        </Empty>
      )}
      <ul className="flex flex-col gap-3">
        {data.gaps.map((gap, i) => {
          const g = normalizeGap(gap);
          return (
            <li key={i}>
              <Card className="py-0">
                <Collapsible>
                  <CollapsibleTrigger asChild>
                    {/* The whole header is the click target (Goran's ruling
                        2026-08-05: entries must be clickable to see what's
                        inside). break-words + min-w-0: a gap question is
                        free-form user text and can be one long unbroken
                        token — without both, CardTitle refuses to shrink
                        below min-content width and blows out the page at
                        narrow widths (confirmed live at 375px). */}
                    <button type="button" className="group w-full cursor-pointer text-left">
                      <CardHeader className="flex-row items-center gap-2 py-4">
                        <div className="min-w-0 flex-1">
                          <CardTitle className="min-w-0 whitespace-pre-wrap break-words text-base font-medium group-data-[state=closed]:line-clamp-2">
                            {g.question}
                          </CardTitle>
                          <CardDescription className="mt-1 flex flex-wrap gap-x-3">
                            <span>{g.timestamp}</span>
                            {g.nearMisses.length > 0 && (
                              <span>
                                {g.nearMisses.length} near miss{g.nearMisses.length === 1 ? "" : "es"}
                              </span>
                            )}
                            {g.codeAnswer && <span>code answer</span>}
                          </CardDescription>
                        </div>
                        <ChevronDown className="shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
                      </CardHeader>
                    </button>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <CardContent className="flex min-w-0 flex-col gap-3 break-words pb-4 text-sm">
                      {g.nearMisses.length > 0 && (
                        <div className="text-muted-foreground">
                          Closest: {g.nearMisses.map((m) => `${m.path} — ${m.heading_path}`).join("; ")}
                        </div>
                      )}
                      {g.codeAnswer && (
                        <div className="flex flex-col gap-2">
                          {g.codeAnswer.notFound ? (
                            <p className="text-muted-foreground">Code analysis also found nothing.</p>
                          ) : (
                            <div className="prose prose-sm max-w-none">
                              <Markdown content={g.codeAnswer.answer} />
                            </div>
                          )}
                          {g.codeAnswer.citations.length > 0 && (
                            <ul className="flex flex-col gap-1 font-mono text-xs text-muted-foreground">
                              {g.codeAnswer.citations.map((c, ci) => (
                                <li key={ci}>{c.file}:{c.lines}</li>
                              ))}
                            </ul>
                          )}
                        </div>
                      )}
                      {g.nearMisses.length === 0 && !g.codeAnswer && (
                        <p className="text-muted-foreground">No further detail recorded for this question.</p>
                      )}
                    </CardContent>
                  </CollapsibleContent>
                </Collapsible>
              </Card>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
