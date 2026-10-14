import AdminLayout from "../components/AdminLayout";
import JiraTicketsView from "~/features/jira/JiraTicketsView";

export default function JiraTicketsPage() {
  return (
    <AdminLayout>
      <JiraTicketsView />
    </AdminLayout>
  );
}
