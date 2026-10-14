import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ChevronRight } from "lucide-react";
import { getResourceHistory, type ResourceLinkChange } from "./api";
import { sessionPath } from "~/features/sessions/api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "~/components/ui/collapsible";
import { Skeleton } from "~/components/ui/skeleton";

const TYPE = {
  environment: "environment",
  service: "service",
  server: "server",
  code_project: "code project",
  deployment: "deployment",
};
const HOW = { person: "person", agent_run: "agent run", discovery: "discovery", migrated: "migrated" };
const CONNECTION = { built_for: "built for", linked: "linked" };

/** One change: a link added (how, by whom, the run that added it) or removed. */
interface Change {
  kind: "Added" | "Removed";
  at: string;
  link: ResourceLinkChange;
}

function changes(history: ResourceLinkChange[]): Change[] {
  return history
    .flatMap((link) => [
      ...(link.removed_at ? [{ kind: "Removed" as const, at: link.removed_at, link }] : []),
      { kind: "Added" as const, at: link.created_at, link },
    ])
    .sort((a, b) => b.at.localeCompare(a.at));
}

function Line({ change: { kind, at, link } }: { change: Change }) {
  const who = kind === "Added" ? link.added_by : link.removed_by;
  const name = `${link.name ?? link.id} (${TYPE[link.type]})`;
  return (
    <li>
      {`${kind} ${name}`}
      {kind === "Added" && (
        <>
          {` · ${CONNECTION[link.connection]} · `}
          {link.added_how === "agent_run" && link.session_id ? (
            <Link className="underline" to={sessionPath(link.session_id)}>
              {HOW.agent_run}
            </Link>
          ) : (
            HOW[link.added_how]
          )}
        </>
      )}
      {` · ${new Date(at).toLocaleString()}`}
      {who ? ` · ${who}` : ""}
    </li>
  );
}

/** Linked resources' collapsible "Link history": every link added or removed, newest first. Loads when first opened and
 * again when `reloadKey` changes while open. */
export default function DocumentLinkHistory({ documentId, reloadKey = 0 }: { documentId: string; reloadKey?: number }) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<ResourceLinkChange[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError("");
    getResourceHistory(documentId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setHistory(value.history);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the link history");
      });
    return () => controller.abort();
  }, [open, documentId, reloadKey]);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-4">
      <CollapsibleTrigger asChild>
        <Button type="button" variant="ghost" size="sm" className="group">
          <ChevronRight data-icon="inline-start" className="transition-transform group-data-[state=open]:rotate-90" />
          Link history
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <section aria-label="Link history" className="mt-2 text-sm">
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : !history ? (
            <Skeleton className="h-16 w-full" />
          ) : history.length === 0 ? (
            <p className="text-muted-foreground">No links yet.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {changes(history).map((change) => (
                <Line key={`${change.kind}-${change.link.type}-${change.link.id}-${change.at}`} change={change} />
              ))}
            </ul>
          )}
        </section>
      </CollapsibleContent>
    </Collapsible>
  );
}
