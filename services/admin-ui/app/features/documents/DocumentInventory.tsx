import { useEffect, useState } from "react";
import { INVENTORY_TYPES, saveDocumentInventory, type InventoryType, type ResourceLink } from "./api";
import { getEntryDocuments } from "~/features/servers/inventoryMap";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";

/** The one server, service or environment a document is built for (an inventory document's resource), or null. */
export function inventoryResource(links: ResourceLink[]) {
  const builtFor = links.filter((link) => link.connection === "built_for");
  const [only] = builtFor;
  return builtFor.length === 1 && INVENTORY_TYPES.includes(only.type as InventoryType)
    ? { ...only, type: only.type as InventoryType }
    : null;
}
const named = (resource: Pick<ResourceLink, "type" | "id" | "name">) =>
  `${resource.type} ${resource.name ?? resource.id}`;
/** "Inventory document of server web-1": the badge naming an inventory document's resource. */
export function inventoryLabel(links: ResourceLink[]) {
  const resource = inventoryResource(links);
  return resource ? `Inventory document of ${named(resource)}` : "Inventory document";
}

/** Linked resources' Mark as inventory document / Unmark. Unmark is offered on an inventory document (its built-for
 * link stays); Mark only on a document built for exactly one server, service or environment that has no inventory
 * document. A refusal shows its message. `onChanged` takes the document's next revision and flag. */
export default function DocumentInventory({
  documentId,
  revision,
  inventory,
  links,
  disabled,
  onChanged,
}: {
  documentId: string;
  revision: number;
  inventory: boolean;
  links: ResourceLink[];
  disabled?: boolean;
  onChanged: (revision: number, inventory: boolean) => void;
}) {
  const resource = inventoryResource(links);
  // Whether the resource already has an inventory document (null: not known yet).
  const [taken, setTaken] = useState<boolean | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setTaken(null);
    if (inventory || !resource) return;
    const controller = new AbortController();
    getEntryDocuments({ kind: resource.type, id: resource.id }, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setTaken(value.inventory_document !== null);
      })
      .catch(() => undefined); // Mark stays hidden
    return () => controller.abort();
  }, [inventory, resource?.type, resource?.id]);
  async function change(next: boolean) {
    setPending(true);
    setError("");
    try {
      const saved = await saveDocumentInventory(documentId, revision, next);
      onChanged(saved.revision, saved.inventory);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change the inventory document");
    } finally {
      setPending(false);
    }
  }
  const offerMark = !inventory && resource !== null && taken === false;
  if (!inventory && !offerMark && !error) return null;
  return (
    <section aria-label="Inventory document" className="mt-4 flex flex-col gap-2 rounded-lg border p-4">
      {inventory ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm">
            {resource ? `This is the inventory document of ${named(resource)}.` : "This is an inventory document."}
          </p>
          <Button type="button" variant="outline" disabled={pending || disabled} onClick={() => void change(false)}>
            Unmark
          </Button>
        </div>
      ) : (
        offerMark && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-muted-foreground">{`Built for ${named(resource)}, which has no inventory document yet.`}</p>
            <Button type="button" variant="outline" disabled={pending || disabled} onClick={() => void change(true)}>
              Mark as inventory document
            </Button>
          </div>
        )
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </section>
  );
}
