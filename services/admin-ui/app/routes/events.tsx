import AdminLayout from "../components/AdminLayout";
import EventsPage from "~/features/events/EventsPage";

export default function EventsRoute() {
  return <AdminLayout><EventsPage tab="events" /></AdminLayout>;
}
