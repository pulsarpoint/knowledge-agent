import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { Copy } from "lucide-react";
import { getSessionDebug, previewSession, type AccessResource, type SessionDebug, type StartInput } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Spinner } from "~/components/ui/spinner";

const TYPE_WORDS: Record<string, string> = {
  server: "Server",
  service: "Service",
  environment: "Environment",
  code_project: "Code project",
  document: "Document",
  mcp_server: "MCP server",
};

/** Where a resource comes from: given to the run, or via the resource it came with (named when the run has it). */
function source(resource: AccessResource, resources: AccessResource[]): string {
  if (resource.source === "given") return "Given";
  const [, type, id] = resource.source.split(" ");
  const parent = resources.find((item) => item.type === type && item.id === id);
  return `Via ${(TYPE_WORDS[type] ?? type).toLowerCase()} ${parent?.name ?? id}`;
}

function Section({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex min-w-0 flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {actions}
      </div>
      {children}
    </section>
  );
}

/** The Debug tab of a chat: exactly what the model receives. A run (`runId`): its pinned configuration (GET
 * /api/sessions/{id}/debug); before the first message (`input`): what the next start would pin (POST
 * /api/sessions/preview). Both are built by the runner's own code. */
export default function DebugPanel({ runId, input }: { runId: string | null; input: StartInput | null }) {
  const [view, setView] = useState<SessionDebug | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const request = runId !== null ? `run:${runId}` : input ? JSON.stringify(input) : null;
  useEffect(() => {
    setView(null);
    setError("");
    if (request === null) return;
    const controller = new AbortController();
    (runId !== null ? getSessionDebug(runId, controller.signal) : previewSession(input!, controller.signal))
      .then((value) => {
        if (!controller.signal.aborted) setView(value);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "Could not load what the model gets.");
      });
    return () => controller.abort();
  }, [request]);

  if (error)
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  if (request === null) return <p className="text-sm text-muted-foreground">Nothing to show.</p>;
  if (!view)
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner /> Loading what the model gets…
      </p>
    );
  const resources = view.access.resources;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {runId === null && (
        <p className="text-xs text-muted-foreground">What the next chat starts with, from the current settings.</p>
      )}
      <Section
        title="Instructions"
        actions={
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() =>
              void navigator.clipboard?.writeText(view.instructions).then(
                () => setCopied(true),
                () => undefined,
              )
            }
          >
            <Copy data-icon="inline-start" />
            {copied ? "Copied" : "Copy"}
          </Button>
        }
      >
        <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 text-xs">
          {view.instructions}
        </pre>
      </Section>
      <Section title="Prompt">
        <p className="text-sm">
          {view.prompt ? (
            <>
              <Link className="underline" to={`/prompts/${encodeURIComponent(view.prompt.id)}`}>
                {view.prompt.name}
              </Link>
              {view.prompt.version !== null
                ? ` · version ${view.prompt.version}`
                : " · edited before the start (no saved version)"}
            </>
          ) : (
            <span className="text-muted-foreground">Not from the prompt library.</span>
          )}
        </p>
        {view.agent_instructions && (
          <div className="flex flex-col gap-1">
            <h4 className="text-xs font-medium text-muted-foreground">Agent instructions</h4>
            <pre className="whitespace-pre-wrap break-words text-xs">{view.agent_instructions}</pre>
          </div>
        )}
        {view.skills.length > 0 && (
          <div className="flex flex-col gap-1">
            <h4 className="text-xs font-medium text-muted-foreground">Skills</h4>
            <ul className="flex flex-wrap gap-1.5 text-xs">
              {view.skills.map((skill) => (
                <li key={skill.name} className="rounded border px-2 py-0.5">
                  {skill.name}
                  {skill.folder && (
                    <span className="ml-1.5 font-mono text-muted-foreground">{skill.folder.split("/").pop()}</span>
                  )}
                </li>
              ))}
            </ul>
            {view.skills_used && view.skills_used.length > 0 && (
              <>
                <h4 className="mt-1 text-xs font-medium text-muted-foreground">Skills used</h4>
                <p data-testid="skills-used" className="text-xs">
                  {view.skills_used.join(", ")}
                </p>
              </>
            )}
          </div>
        )}
      </Section>
      <Section title="Tools">
        {view.tools.length === 0 ? (
          <p className="text-sm text-muted-foreground">No tools.</p>
        ) : (
          <ul className="flex flex-col divide-y rounded-md border">
            {view.tools.map((tool) => (
              <li key={tool.name} className="flex flex-col gap-1 px-3 py-2">
                <span className="font-mono text-xs font-medium">{tool.name}</span>
                <span className="whitespace-pre-wrap break-words text-xs text-muted-foreground">
                  {tool.description}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Resources">
        {resources.length === 0 ? (
          <p className="text-sm text-muted-foreground">None.</p>
        ) : (
          <ul className="flex flex-col divide-y rounded-md border">
            {resources.map((resource) => (
              <li
                key={`${resource.type}:${resource.id}`}
                className="flex min-w-0 flex-wrap items-baseline gap-x-2 px-3 py-2 text-sm"
              >
                <span className="text-muted-foreground">{TYPE_WORDS[resource.type] ?? resource.type}</span>
                <span className="break-words font-medium">{resource.name}</span>
                <span className="ml-auto text-xs text-muted-foreground">{source(resource, resources)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Model">
        <p className="text-sm">{view.model?.name ?? "No model"}</p>
      </Section>
    </div>
  );
}
