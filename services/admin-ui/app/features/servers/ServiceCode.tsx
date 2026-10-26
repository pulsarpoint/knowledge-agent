import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getRepositories, type Repository } from "~/features/codeProjects/api";
import { createItem, draftsUsed } from "./inventory";
import { builtFrom, useServicePage } from "./ServiceLayout";
import { draftOf, List, Row } from "./ServicePanel";
import PickSheet from "./PickSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";

/** Where a code project's source is: its Git remote, or its local folders. */
const source = (repository: { remote_url: string | null }) =>
  repository.remote_url?.replace(/\.git$/, "") ?? "Local folders";

/** The code projects the service is built from: each links to its page, with Remove (asked first) and, for a draft, its
 * badge with Confirm / Discard; Add picks a code project in a sheet with search. */
export default function ServiceCode() {
  const { service, services, relations, changed, ask } = useServicePage();
  const [repositories, setRepositories] = useState<Repository[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getRepositories(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setRepositories(value.repositories);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setLoadError(e instanceof ApiError ? e.message : "Could not load the code projects.");
      });
    return () => controller.abort();
  }, []);

  const known = new Map<string, { name: string; remote_url: string | null }>(
    [...(repositories ?? []), ...service.repositories].map((item) => [item.id, item]),
  );
  const rows = builtFrom(service.id, relations).map((item) => {
    const id = item.to_code_project_id ?? "";
    return { item, id, project: known.get(id) };
  });
  const linked = new Set(rows.map((row) => row.id));
  const choices = loadError
    ? []
    : (repositories
        ?.filter((item) => !linked.has(item.id))
        .map((item) => ({ id: item.id, name: item.name, detail: source(item) })) ?? null);

  return (
    <section aria-label="Built from" className="flex min-w-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-lg font-semibold">Built from</h3>
        <Button type="button" size="sm" variant="outline" onClick={() => setAdding(true)}>
          <Plus data-icon="inline-start" />
          Add
        </Button>
      </div>
      {loadError && (
        <Alert variant="destructive">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      )}
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No code projects recorded.</p>
      ) : (
        <List>
          {rows.map(({ item, id, project }) => {
            const name = project?.name ?? "an unknown code project";
            return (
              <Row
                key={item.id}
                text={name}
                href={`/code-projects/${encodeURIComponent(id)}`}
                detail={project ? source(project) : undefined}
                name={name}
                draft={draftOf("relation", item)}
                onDone={changed}
                onRemove={() =>
                  ask(
                    "relations",
                    item,
                    `code project ${name} from ${service.name}`,
                    "The code project stays; only the link goes.",
                  )
                }
              />
            );
          })}
        </List>
      )}
      {adding && (
        <PickSheet
          title="Add a code project"
          description={`A code project ${service.name} is built from.`}
          searchLabel="Search code projects"
          choices={choices}
          drafts={() => draftsUsed(services, { services: [service.id] })}
          add={(id, description) =>
            createItem("relations", {
              from_service_id: service.id,
              kind: "built_from",
              to_service_id: null,
              to_code_project_id: id,
              description,
            })
          }
          onAdded={() => {
            setAdding(false);
            changed();
          }}
          onClose={() => setAdding(false)}
        />
      )}
    </section>
  );
}
