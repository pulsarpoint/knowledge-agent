import AdminLayout from "../components/AdminLayout";
import SystemMapView from "~/features/servers/SystemMapView";

export default function SystemMapRoute() {
  return (
    <AdminLayout>
      <SystemMapView />
    </AdminLayout>
  );
}
