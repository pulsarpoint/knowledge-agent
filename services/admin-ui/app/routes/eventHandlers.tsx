import AdminLayout from "../components/AdminLayout";
import EventsPage from "../components/EventsPage";

export default function EventHandlersRoute() {
  return <AdminLayout><EventsPage tab="handlers" /></AdminLayout>;
}
