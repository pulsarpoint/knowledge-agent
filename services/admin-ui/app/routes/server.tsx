import AdminLayout from "../components/AdminLayout";
import ServerLayout from "~/features/servers/ServerPage";

/** A server's page: its header and sub-pages; the open one is a child route. */
export default function Page() {
  return (
    <AdminLayout>
      <ServerLayout />
    </AdminLayout>
  );
}
