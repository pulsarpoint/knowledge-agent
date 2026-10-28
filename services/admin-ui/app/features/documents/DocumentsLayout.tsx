import { Link, Outlet, useSearchParams } from "react-router";
import { Plus } from "lucide-react";
import SectionLinks from "~/components/SectionLinks";
import { Button } from "~/components/ui/button";
import { draftFilter } from "./DocumentsView";

/** Documents (`/documents`): the sub-pages Documents and Drafts (`/documents/drafts`) as links with New document, above
 * the open one. The links carry the resource and tag filters; search, Has drafts and paging belong to the documents
 * list. */
export default function DocumentsLayout() {
  const [params] = useSearchParams();
  const filters = new URLSearchParams(draftFilter(params)).toString();
  const search = filters ? `?${filters}` : "";
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <SectionLinks
          label="Documents sections"
          sections={[
            {
              to: { pathname: "/documents", search },
              label: "Documents",
              end: true,
              help: "The live documents, with their links, versions and drafts.",
            },
            {
              to: { pathname: "/documents/drafts", search },
              label: "Drafts",
              help: "Every draft, of new documents and of live ones, waiting to be published or discarded.",
            },
          ]}
        />
        <Button asChild>
          <Link to="/documents/new">
            <Plus data-icon="inline-start" />
            New document
          </Link>
        </Button>
      </div>
      <Outlet />
    </div>
  );
}
