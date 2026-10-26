import AdminLayout from "../components/AdminLayout";
import ServiceLayout from "~/features/servers/ServiceLayout";

/** A service's page: its header and sub-pages; the open one is a child route. */
export default function ServicePage() {
  return (
    <AdminLayout>
      <ServiceLayout />
    </AdminLayout>
  );
}
