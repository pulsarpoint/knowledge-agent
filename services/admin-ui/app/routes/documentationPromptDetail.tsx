import { Navigate, useParams } from "react-router";
export default function Page() { const { promptId } = useParams(); return <Navigate to={`/prompts/${promptId ?? ""}`} replace />; }
