import { Outlet } from "react-router";
import SectionLinks from "~/components/SectionLinks";

const SECTIONS = [
  {
    to: "/events",
    label: "Events",
    end: true,
    help: "What happened at your sources, newest first, and what each event led to.",
  },
  {
    to: "/events/handlers",
    label: "Handlers",
    help: "Rules that turn a matching event into a new event or an agent's answer.",
  },
];

/** Administration → Events (`/events`): the sub-pages Events and Handlers (`/events/handlers`) as links, above the open
 * one. */
export default function EventsLayout() {
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <SectionLinks label="Events sections" sections={SECTIONS} />
      <Outlet />
    </div>
  );
}
