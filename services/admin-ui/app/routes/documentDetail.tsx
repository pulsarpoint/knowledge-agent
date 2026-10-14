import AdminLayout from "../components/AdminLayout";
import DocumentEditorView from "~/features/documents/DocumentEditorView";

export default function Page() {
  return (
    <AdminLayout>
      <DocumentEditorView />
    </AdminLayout>
  );
}
