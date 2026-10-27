import { Outlet, useSearchParams } from "react-router";
import BackLink from "~/components/BackLink";
import SectionLinks from "~/components/SectionLinks";
import { newDocumentResource, newDocumentSearch } from "./api";

/** Documents → New: Write (the form) and Write with an agent (a chat), sub-pages with tab links. A resource's New
 * document (?resource=<type>&resource_id=<id>) keeps its resource on both. */
export default function NewDocumentLayout() {
  const [params] = useSearchParams();
  const search = newDocumentSearch(newDocumentResource(params));
  // the agent tab keeps its open conversation (?chat=)
  const chat = params.get("chat");
  const agentSearch = chat ? `${search ? `${search}&` : "?"}${new URLSearchParams({ chat })}` : search;
  return (
    <section className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-col gap-1">
        <BackLink to="/documents">All documents</BackLink>
        <h2 className="text-xl font-semibold">New document</h2>
      </div>
      <SectionLinks
        label="New document"
        sections={[
          { to: { pathname: "/documents/new", search }, label: "Write", end: true },
          { to: { pathname: "/documents/new/agent", search: agentSearch }, label: "Write with an agent" },
        ]}
      />
      <Outlet />
    </section>
  );
}
