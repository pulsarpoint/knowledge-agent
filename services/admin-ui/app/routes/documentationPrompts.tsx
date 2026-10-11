import { Navigate, useLocation } from "react-router";
export default function Page() {
  const { search, hash } = useLocation();
  return <Navigate to={{ pathname: "/prompts", search, hash }} replace />;
}
