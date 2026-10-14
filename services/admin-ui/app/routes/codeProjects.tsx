import AdminLayout from "../components/AdminLayout";
import CodeProjectsView from "~/features/codeProjects/CodeProjectsView";

export default function CodeProjectsPage() {
  return (
    <AdminLayout>
      <CodeProjectsView />
    </AdminLayout>
  );
}
