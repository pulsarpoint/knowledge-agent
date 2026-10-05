import { Navigate, useLocation } from "react-router";

export default function LegacyAdminPage() {
  const { pathname, search, hash } = useLocation();
  return <Navigate to={{ pathname: pathname.slice("/docs".length) || "/rag/collections", search, hash }} replace />;
}
