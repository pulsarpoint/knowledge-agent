import AdminLayout from "../components/AdminLayout";
import CodeProjectLayout from "~/features/codeProjects/CodeProjectLayout";

/** A code project's page: its header and sub-pages; the open one (Overview, Documents or Settings) is a child route. */
export default function CodeProjectPage() {
  return (
    <AdminLayout>
      <CodeProjectLayout />
    </AdminLayout>
  );
}
