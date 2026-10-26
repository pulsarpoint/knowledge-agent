import AdminLayout from "../components/AdminLayout";
import AgentLayout from "~/features/agents/AgentLayout";

/** The agent page: its header and sections; the open section (General, Resources or Chat) is a child route. */
export default function AgentDetailPage() {
  return (
    <AdminLayout>
      <AgentLayout />
    </AdminLayout>
  );
}
