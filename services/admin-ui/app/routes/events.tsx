import AdminLayout from "../components/AdminLayout";
import EventsLayout from "~/features/events/EventsPage";

/** Events: the section links; the open sub-page (Events or Handlers) is a child route. */
export default function EventsRoute() {
  return (
    <AdminLayout>
      <EventsLayout />
    </AdminLayout>
  );
}
