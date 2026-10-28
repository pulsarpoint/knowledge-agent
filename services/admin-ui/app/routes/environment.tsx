import AdminLayout from "../components/AdminLayout";
import EnvironmentLayout from "~/features/servers/EnvironmentPage";

/** An environment's page: its header and sub-pages; the open one is a child route. */
export default function Page() {
  return (
    <AdminLayout>
      <EnvironmentLayout />
    </AdminLayout>
  );
}
