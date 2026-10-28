import { Navigate, useLocation, useSearchParams } from "react-router";

/** A page's index sub-page: opens the sub-page an old link (`?<param>=<value>`, `param` "view" by default) named
 * through `pages` (value → sub-page), else `fallback`. The rest of the query (an open chat) and the hash are kept;
 * `state` goes with the navigation. */
export default function OldLinkRedirect({
  param = "view",
  pages,
  fallback,
  state,
}: {
  param?: string;
  pages: Record<string, string>;
  fallback: string;
  state?: unknown;
}) {
  const [current] = useSearchParams();
  const { hash } = useLocation();
  const params = new URLSearchParams(current);
  const value = params.get(param) ?? "";
  params.delete(param);
  const query = params.toString();
  const page = Object.hasOwn(pages, value) ? pages[value] : fallback;
  return <Navigate replace to={`${page}${query ? `?${query}` : ""}${hash}`} state={state} />;
}
