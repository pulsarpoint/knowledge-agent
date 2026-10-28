import OldLinkRedirect from "~/components/OldLinkRedirect";

/** `/services/:serviceId`: opens View; an old link (`?view=overview|documents`) opens its sub-page, keeping the rest of
 * the query (an open chat). */
export default function ServiceIndex() {
  return <OldLinkRedirect pages={{ documents: "documents" }} fallback="view" />;
}
