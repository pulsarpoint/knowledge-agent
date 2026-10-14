import AdminLayout from "../components/AdminLayout";
import NotificationsView from "~/features/events/NotificationsView";

export default function NotificationsRoute() {
  return (
    <AdminLayout>
      <NotificationsView />
    </AdminLayout>
  );
}
