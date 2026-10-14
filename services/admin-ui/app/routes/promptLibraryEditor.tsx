import AdminLayout from "../components/AdminLayout";
import { DocumentationPromptEditorView } from "~/features/documents/DocumentationPromptsView";

export default function Page() {
  return (
    <AdminLayout>
      <DocumentationPromptEditorView />
    </AdminLayout>
  );
}
