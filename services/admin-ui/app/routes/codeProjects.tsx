import AdminLayout from "../components/AdminLayout";
import CodeProjectsLayout from "~/features/codeProjects/CodeProjectsLayout";

/** Code projects: the open sub-page (Projects or Code folder) is a child route. */
export default function CodeProjectsPage() {
  return (
    <AdminLayout>
      <CodeProjectsLayout />
    </AdminLayout>
  );
}
