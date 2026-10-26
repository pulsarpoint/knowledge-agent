import { Navigate, useLocation, useSearchParams } from "react-router";

/** `/services/:serviceId`: opens View; an old link (`?view=overview|documents`) opens its sub-page, keeping the rest of
 * the query (an open chat). */
export default function ServiceIndex() {
  const [current] = useSearchParams();
  const { hash } = useLocation();
  const params = new URLSearchParams(current);
  const page = params.get("view") === "documents" ? "documents" : "view";
  params.delete("view");
  const query = params.toString();
  return <Navigate replace to={`${page}${query ? `?${query}` : ""}${hash}`} />;
}
