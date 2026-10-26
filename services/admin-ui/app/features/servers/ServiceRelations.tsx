import { useState } from "react";
import { Plus } from "lucide-react";
import { createItem, draftsUsed, relationText } from "./inventory";
import { RELATION_TABS, tabRelations, useServicePage, type RelationTab } from "./ServiceLayout";
import { servicePath } from "./ServicePanel";
import { draftOf, List, Row } from "./rows";
import PickSheet from "./PickSheet";
import { Button } from "~/components/ui/button";

/** What each tab's Add sheet asks, for the service `name`. */
const QUESTIONS: Record<RelationTab, (name: string) => string> = {
  "depends-on": (name) => `What ${name} depends on`,
  "used-by": (name) => `What depends on ${name}`,
  "part-of": (name) => `What ${name} is part of`,
  parts: (name) => `What is part of ${name}`,
};

/** One relation tab: the services at the other end, each with its description, a link to its page, Remove (asked
 * first) and, for a draft, its badge with Confirm / Discard; Add picks another service in a sheet with search. Used by
 * and Parts add the relation from the chosen service to this one. */
function RelationList({ tab }: { tab: RelationTab }) {
  const { service, services, relations, changed, ask } = useServicePage();
  const [adding, setAdding] = useState(false);
  const { label, kind, outgoing } = RELATION_TABS[tab];
  const names = new Map(services.map((item) => [item.id, item.name]));
  const rows = tabRelations(service.id, relations, tab).map((item) => {
    const other = (outgoing ? item.to_service_id : item.from_service_id) ?? "";
    return { item, other, name: names.get(other) ?? "an unknown service" };
  });
  const related = new Set(rows.map((row) => row.other));
  const choices = services
    .filter((item) => item.id !== service.id && !related.has(item.id))
    .map(({ id, name }) => ({ id, name }));

  return (
    <section aria-label={label} className="flex min-w-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-lg font-semibold">{label}</h3>
        <Button type="button" size="sm" variant="outline" onClick={() => setAdding(true)}>
          <Plus data-icon="inline-start" />
          Add
        </Button>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">None recorded.</p>
      ) : (
        <List>
          {rows.map(({ item, other, name }) => {
            const text = relationText(item, names);
            return (
              <Row
                key={item.id}
                text={name}
                href={other ? servicePath(other) : undefined}
                detail={item.description}
                name={name}
                draft={draftOf("relation", item)}
                onDone={changed}
                onRemove={() => ask("relations", item, `“${text}”`, "Both services stay; only this relation goes.")}
              />
            );
          })}
        </List>
      )}
      {adding && (
        <PickSheet
          title={QUESTIONS[tab](service.name)}
          searchLabel="Search services"
          choices={choices}
          drafts={(id) => draftsUsed(services, { services: [service.id, id] })}
          add={(id, description) =>
            createItem("relations", {
              from_service_id: outgoing ? service.id : id,
              kind,
              to_service_id: outgoing ? id : service.id,
              to_code_project_id: null,
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

export const DependsOn = () => <RelationList tab="depends-on" />;
export const UsedBy = () => <RelationList tab="used-by" />;
export const PartOf = () => <RelationList tab="part-of" />;
export const Parts = () => <RelationList tab="parts" />;
