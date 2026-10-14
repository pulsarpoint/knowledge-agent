import type { DocumentInput, DocumentResources, ResourceLink } from "./api";
import { DOCUMENT_KINDS } from "~/lib/resources";
import ResourcePicker, { deploymentCascade } from "~/components/ResourcePicker";

/** The picker's kind key of a link's resource type (environment → environments, code_project → code_projects). */
export const kindOfLink = (link: Pick<ResourceLink, "type">) => `${link.type}s`;

/** A document's Linked resources. The resources it was built for (`builtFor`) stay ticked and locked ("Built for this
 * document"): even a cascade (unticking a deployment's service or server) keeps them. */
export default function DocumentResourceSelection({
  resources,
  value,
  onChange,
  builtFor = [],
}: {
  resources: DocumentResources;
  value: DocumentInput;
  onChange: (value: DocumentInput) => void;
  builtFor?: Pick<ResourceLink, "type" | "id">[];
}) {
  const locked = (kind: string, id: string) => builtFor.some((link) => kindOfLink(link) === kind && link.id === id);
  const cascade = deploymentCascade<DocumentInput>(resources.deployments);
  return (
    <ResourcePicker
      kinds={DOCUMENT_KINDS}
      options={resources}
      value={value}
      onChange={onChange}
      idPrefix="doc"
      layout="cards"
      showUnavailable
      itemState={(kind, item) => (locked(kind, item.id) ? { locked: true, note: "Built for this document" } : {})}
      description={({ key, plural }) =>
        key === "deployments"
          ? "Select a deployment to distinguish environments. Its service and server are selected too."
          : `Link the ${plural.toLowerCase()} this document describes.`
      }
      emptyText={({ plural }) => `No ${plural.toLowerCase()} available.`}
      cascade={(next, change) => {
        const result = cascade(next, change);
        const keep = builtFor.filter((link) => link.type === "deployment").map((link) => link.id);
        return keep.length ? { ...result, deployment_ids: [...new Set([...result.deployment_ids, ...keep])] } : result;
      }}
    />
  );
}
