import { Navigate, useLocation, useParams } from "react-router";
export default function Page() {
  const { promptId } = useParams();
  const { search, hash } = useLocation();
  return <Navigate to={{ pathname: `/prompts/${promptId ?? ""}`, search, hash }} replace />;
}
