import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Plus } from "lucide-react";
import { ApiError } from "~/lib/api";
import {
  getCodeDependencies,
  getRepositories,
  type CodeDependencies,
  type CodeDependency,
  type Repository,
} from "./api";
import { useCodeProjectPage } from "./CodeProjectLayout";
import { createItem } from "~/features/servers/inventory";
import { draftOf, List, Row, removeItem } from "~/features/servers/rows";
import ConfirmRemove, { type Removal } from "~/components/ConfirmRemove";
import PickSheet from "~/features/servers/PickSheet";
import { DraftBadge } from "~/features/servers/ConfirmDraftSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";

const message = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const projectPath = (id: string) => `/code-projects/${encodeURIComponent(id)}`;

/** `/code-projects/:codeProjectId/dependencies`: Depends on (the libraries it imports: each links to its page, with
 * its description, Remove (asked first) and, for a draft, its badge with Confirm / Discard; Add picks a code project in
 * a sheet with search) and Used by (the code projects that depend on it and the services built from them: read-only
 * links). */
export function CodeProjectDependenciesPage() {
  const { project, version } = useCodeProjectPage();
  const [data, setData] = useState<CodeDependencies | null>(null);
  const [repositories, setRepositories] = useState<Repository[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  const [adding, setAdding] = useState(false);
  const [removal, setRemoval] = useState<Removal | null>(null);
  const changed = () => {
    setError(null);
    setReloads((value) => value + 1);
  };

  useEffect(() => {
    const controller = new AbortController();
    getCodeDependencies(project.id, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e, "Could not load the dependencies."));
      });
    return () => controller.abort();
  }, [project.id, version, reloads]);
  useEffect(() => {
    if (!adding) return;
    const controller = new AbortController();
    getRepositories(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setRepositories(value.repositories);
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setRepositories([]);
          setError(message(e, "Could not load the code projects."));
        }
      });
    return () => controller.abort();
  }, [adding]);

  const remove = (item: CodeDependency) => removeItem("code-dependencies", item, changed, setError);

  const listed = new Set(data?.depends_on.map((item) => item.code_project.id));
  const choices =
    repositories
      ?.filter((item) => item.id !== project.id && !listed.has(item.id))
      .map((item) => ({ id: item.id, name: item.name, detail: item.remote_url?.replace(/\.git$/, "") })) ?? null;

  return (
    <div className="flex min-w-0 flex-col gap-6">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <section aria-label="Depends on" className="flex min-w-0 flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex flex-col gap-1">
            <h3 className="text-lg font-semibold">Depends on</h3>
            <p className="text-sm text-muted-foreground">Code projects this one uses as libraries.</p>
          </div>
          <Button type="button" size="sm" variant="outline" onClick={() => setAdding(true)}>
            <Plus data-icon="inline-start" />
            Add
          </Button>
        </div>
        {!data ? null : data.depends_on.length === 0 ? (
          <p className="text-sm text-muted-foreground">No libraries recorded.</p>
        ) : (
          <List>
            {data.depends_on.map((item) => (
              <Row
                key={item.id}
                text={item.code_project.name}
                href={projectPath(item.code_project.id)}
                detail={item.description}
                name={item.code_project.name}
                draft={draftOf("code_dependency", item)}
                onDone={changed}
                onRemove={() =>
                  setRemoval({
                    title: `Remove the dependency on ${item.code_project.name}?`,
                    description: "Both code projects stay; only the dependency goes.",
                    action: "Remove",
                    run: () => void remove(item),
                  })
                }
              />
            ))}
          </List>
        )}
      </section>
      <section aria-label="Used by" className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h3 className="text-lg font-semibold">Used by</h3>
          <p className="text-sm text-muted-foreground">
            Code projects that use this one, and the services built from them: they are affected when it changes.
          </p>
        </div>
        {data &&
          (data.used_by.code_projects.length === 0 && data.used_by.services.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing is recorded as using this code project.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm">
              {data.used_by.code_projects.map((item) => (
                <li key={item.id} className="flex items-center gap-2">
                  <Link className="underline-offset-4 hover:underline" to={projectPath(item.code_project.id)}>
                    {item.code_project.name}
                  </Link>
                  <span className="text-muted-foreground">code project</span>
                  {item.status === "draft" && <DraftBadge />}
                </li>
              ))}
              {data.used_by.services.map((item) => (
                <li key={item.id} className="flex items-center gap-2">
                  <Link
                    className="underline-offset-4 hover:underline"
                    to={`/services/${encodeURIComponent(item.id)}/view`}
                  >
                    {item.name}
                  </Link>
                  <span className="text-muted-foreground">{`service, via ${item.via.map((via) => via.name).join(", ")}`}</span>
                  {(item.status === "draft" || item.via.every((via) => via.status === "draft")) && <DraftBadge />}
                </li>
              ))}
            </ul>
          ))}
      </section>
      {adding && (
        <PickSheet
          title="Add a dependency"
          description={`A code project ${project.name} depends on.`}
          searchLabel="Search code projects"
          choices={choices}
          add={(id, description) =>
            createItem("code-dependencies", {
              from_code_project_id: project.id,
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
      <ConfirmRemove removal={removal} onClose={() => setRemoval(null)} />
    </div>
  );
}
