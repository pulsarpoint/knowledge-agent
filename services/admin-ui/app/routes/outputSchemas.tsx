import AdminLayout from "../components/AdminLayout";
import OutputSchemasView from "~/features/outputSchemas/OutputSchemasView";

export default function Page() {
  return (
    <AdminLayout>
      <OutputSchemasView />
    </AdminLayout>
  );
}
