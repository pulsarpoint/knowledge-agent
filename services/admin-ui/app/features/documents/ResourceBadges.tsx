import { Link } from "react-router";
import type { ResourceType } from "./api";
import { resourceChatPath } from "~/features/sessions/api";
import { badgeVariants } from "~/components/ui/badge";
import { cn } from "~/lib/utils";

/** The kinds of resource a document links, in the order its links are listed, with their labels. */
export const LINK_TYPES: { type: ResourceType; label: string; plural: string }[] = [
  { type: "environment", label: "Environment", plural: "Environments" },
  { type: "server", label: "Server", plural: "Servers" },
  { type: "service", label: "Service", plural: "Services" },
  { type: "code_project", label: "Code project", plural: "Code projects" },
  { type: "deployment", label: "Deployment", plural: "Deployments" },
];
export const typeLabel = (type: ResourceType) => LINK_TYPES.find((item) => item.type === type)?.label ?? type;
/** A resource type in words: "code project". */
export const typeWord = (type: ResourceType) => typeLabel(type).toLowerCase();

interface Linkable {
  type: ResourceType;
  id: string;
  name: string | null;
  service_id?: string | null;
}
/** A linked resource's page (a deployment opens its service's; null when that is unknown). */
export const linkedResourcePath = (link: Linkable) =>
  link.type !== "deployment"
    ? resourceChatPath({ type: link.type, id: link.id })
    : link.service_id
      ? resourceChatPath({ type: "service", id: link.service_id })
      : null;
/** Links by type (LINK_TYPES), then name. */
export const byTypeThenName = <T extends Linkable>(links: T[]) =>
  [...links].sort(
    (a, b) =>
      LINK_TYPES.findIndex((item) => item.type === a.type) - LINK_TYPES.findIndex((item) => item.type === b.type) ||
      (a.name ?? a.id).localeCompare(b.name ?? b.id),
  );

const SHOWN = 4;
const badge = cn(badgeVariants({ variant: "outline" }), "text-xs font-normal hover:bg-accent");

/** The document header's badges of its active resource links (`Environment · dev`), each linking to the resource; past
 * four, "+N" links to the document's Connections page. */
export default function ResourceBadges({ links, connectionsPath }: { links: Linkable[]; connectionsPath: string }) {
  if (links.length === 0) return null;
  const sorted = byTypeThenName(links);
  const more = sorted.length - SHOWN;
  return (
    <span role="group" aria-label="Linked resources" className="inline-flex flex-wrap items-center gap-1">
      {sorted.slice(0, SHOWN).map((link) => {
        const text = `${typeLabel(link.type)} · ${link.name ?? link.id}`;
        const to = linkedResourcePath(link);
        return to ? (
          <Link key={`${link.type}-${link.id}`} to={to} className={badge}>
            {text}
          </Link>
        ) : (
          <span key={`${link.type}-${link.id}`} className={badge}>
            {text}
          </span>
        );
      })}
      {more > 0 && (
        <Link to={connectionsPath} className={badge} title={`${more} more on Connections`}>
          {`+${more}`}
        </Link>
      )}
    </span>
  );
}
