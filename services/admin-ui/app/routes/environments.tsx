import AdminLayout from "../components/AdminLayout";
import EnvironmentsView from "~/features/servers/EnvironmentsView";

export default function EnvironmentsPage() {
  return (
    <AdminLayout>
      <EnvironmentsView />
    </AdminLayout>
  );
}
