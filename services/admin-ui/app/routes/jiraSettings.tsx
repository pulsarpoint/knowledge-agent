import AdminLayout from "../components/AdminLayout";
import JiraSettingsView from "~/features/jira/JiraSettingsView";

export default function JiraSettingsPage() {
  return (
    <AdminLayout>
      <JiraSettingsView />
    </AdminLayout>
  );
}
