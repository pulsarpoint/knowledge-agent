import AdminLayout from "../components/AdminLayout";
import EventsPage from "~/features/events/EventsPage";

export default function EventHandlersRoute() {
  return <AdminLayout><EventsPage tab="handlers" /></AdminLayout>;
}
