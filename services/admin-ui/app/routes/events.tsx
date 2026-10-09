import AdminLayout from "../components/AdminLayout";
import EventsPage from "../components/EventsPage";

export default function EventsRoute() {
  return <AdminLayout><EventsPage tab="events" /></AdminLayout>;
}
