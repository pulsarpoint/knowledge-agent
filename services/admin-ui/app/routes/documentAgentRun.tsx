import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import DocumentAgentRunView from "../components/DocumentAgentRunView";

export default function Page() {
  const { documentId, runId } = useParams();
  return <AdminLayout><DocumentAgentRunView key={runId} documentId={documentId!} runId={runId!} /></AdminLayout>;
}
