import AdminLayout from "../components/AdminLayout";
import NewDocumentLayout from "~/features/documents/NewDocumentLayout";

/** Documents → New: its tab links; the open sub-page (Write or Write with an agent) is a child route. */
export default function Page() {
  return (
    <AdminLayout>
      <NewDocumentLayout />
    </AdminLayout>
  );
}
