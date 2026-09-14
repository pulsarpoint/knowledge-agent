import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import AdminSessionView from "../components/AdminSessionView";

export default function AdminSessionPage() {
  const { key } = useParams() as { key: string };
  return <AdminLayout><AdminSessionView sessionKey={key} /></AdminLayout>;
}
