import AdminLayout from "../components/AdminLayout";
import OutputSchemaEditorView from "~/features/outputSchemas/OutputSchemaEditorView";

export default function Page() {
  return (
    <AdminLayout>
      <OutputSchemaEditorView />
    </AdminLayout>
  );
}
