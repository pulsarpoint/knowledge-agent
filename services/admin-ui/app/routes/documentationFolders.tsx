import { Navigate } from "react-router";

/** Documentation folders are gone (documents are organized by their resources, tags and search): an old link opens the
 * Documents list. */
export default function Page() {
  return <Navigate replace to="/documents" />;
}
