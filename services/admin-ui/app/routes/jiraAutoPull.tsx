import AdminLayout from "../components/AdminLayout";
import JiraAutoPullView from "~/features/jira/JiraAutoPullView";

export default function JiraAutoPullPage() {
  return (
    <AdminLayout>
      <JiraAutoPullView />
    </AdminLayout>
  );
}
