import { Outlet, useSearchParams } from "react-router";
import SectionLinks from "~/components/SectionLinks";
import { newDocumentResource, newDocumentSearch } from "./api";

/** Documents → New: Write (the form) and Write with an agent (a chat), sub-pages with tab links. A resource's New
 * document (?resource=<type>&resource_id=<id>) keeps its resource on both. */
export default function NewDocumentLayout() {
  const [params] = useSearchParams();
  const search = newDocumentSearch(newDocumentResource(params));
  return (
    <section className="flex min-w-0 flex-col gap-4">
      <h1 className="text-2xl font-semibold">New document</h1>
      <SectionLinks
        label="New document"
        sections={[
          { to: { pathname: "/documents/new", search }, label: "Write", end: true },
          { to: { pathname: "/documents/new/agent", search }, label: "Write with an agent" },
        ]}
      />
      <Outlet />
    </section>
  );
}
