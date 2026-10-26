import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router";
import { Plus } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getCodeDependencies, getRepositories, type Repository } from "~/features/codeProjects/api";
import { createItem, draftsUsed } from "./inventory";
import { builtFrom, useServicePage } from "./ServiceLayout";
import { draftOf, List, Row } from "./rows";
import PickSheet from "./PickSheet";
import { DraftBadge } from "./ConfirmDraftSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";

type Lib = { id: string; name: string; status: "draft" | "confirmed" };

/** Where a code project's source is: its Git remote, or its local folders. */
const source = (repository: { remote_url: string | null }) =>
  repository.remote_url?.replace(/\.git$/, "") ?? "Local folders";

/** The code projects the service is built from: each links to its page (its libraries, read-only, under it), with Remove (asked first) and, for a draft, its
 * badge with Confirm / Discard; Add picks a code project in a sheet with search. */
export default function ServiceCode() {
  const { service, services, relations, version, changed, ask } = useServicePage();
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

  // The libraries (what each code project depends on), by code project id; a failed load shows none.
  const [libraries, setLibraries] = useState<Map<string, Lib[]>>(new Map());
  const ids = builtFrom(service.id, relations)
    .map((item) => item.to_code_project_id ?? "")
    .join(",");
  useEffect(() => {
    const controller = new AbortController();
    Promise.all(
      ids
        .split(",")
        .filter(Boolean)
        .map((id) =>
          getCodeDependencies(id, controller.signal)
            .then(
              (value) =>
                [id, value.depends_on.map((item) => ({ ...item.code_project, status: item.status }))] as [
                  string,
                  Lib[],
                ],
            )
            .catch(() => [id, []] as [string, Lib[]]),
        ),
    ).then((entries) => {
      if (!controller.signal.aborted) setLibraries(new Map(entries));
    });
    return () => controller.abort();
  }, [ids, version]);

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
            const libs = libraries.get(id) ?? [];
            return (
              <Fragment key={item.id}>
                <Row
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
                {libs.length > 0 && (
                  <li className="flex flex-wrap gap-x-2 pl-4 text-sm text-muted-foreground">
                    <span>Libraries</span>
                    {libs.map((lib) => (
                      <span key={lib.id} className="flex items-center gap-1">
                        <Link
                          className="underline-offset-4 hover:underline"
                          to={`/code-projects/${encodeURIComponent(lib.id)}`}
                        >
                          {lib.name}
                        </Link>
                        {lib.status === "draft" && <DraftBadge />}
                      </span>
                    ))}
                  </li>
                )}
              </Fragment>
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
