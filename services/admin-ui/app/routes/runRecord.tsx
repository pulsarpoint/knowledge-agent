import AdminLayout from "../components/AdminLayout";
import RunRecordLayout from "~/features/sessions/RunRecordPage";

/** A run record's page: the way back and its sub-pages; the open one is a child route. */
export default function RunRecordPage() {
  return (
    <AdminLayout>
      <RunRecordLayout />
    </AdminLayout>
  );
}
