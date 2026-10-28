import AdminLayout from "../components/AdminLayout";
import DocumentsLayout from "~/features/documents/DocumentsLayout";

/** Documents: the section links; the open sub-page (Documents or Drafts) is a child route. */
export default function Page() {
  return (
    <AdminLayout>
      <DocumentsLayout />
    </AdminLayout>
  );
}
