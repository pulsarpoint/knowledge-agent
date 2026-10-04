import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import RunRecordView from "../components/RunRecordView";

export default function RunRecordPage() {
  const { runKey } = useParams() as { runKey: string };
  return <AdminLayout><RunRecordView runKey={runKey} /></AdminLayout>;
}
