import AdminLayout from "../components/AdminLayout";
import { ServiceNew } from "~/features/servers/ServiceEdit";

export default function ServiceNewPage() {
  return (
    <AdminLayout>
      <ServiceNew />
    </AdminLayout>
  );
}
