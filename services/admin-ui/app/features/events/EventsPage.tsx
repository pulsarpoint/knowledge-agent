import { useNavigate } from "react-router";
import EventsView from "./EventsView";
import HandlersView from "./HandlersView";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";

/** Administration → Events: the Events and Handlers tabs, each its own address (/events, /events/handlers). */
export default function EventsPage({ tab }: { tab: "events" | "handlers" }) {
  const navigate = useNavigate();
  return <Tabs value={tab} onValueChange={value => navigate(value === "handlers" ? "/events/handlers" : "/events")} className="flex flex-col gap-4">
    <TabsList><TabsTrigger value="events">Events</TabsTrigger><TabsTrigger value="handlers">Handlers</TabsTrigger></TabsList>
    <TabsContent value="events">{tab === "events" && <EventsView />}</TabsContent>
    <TabsContent value="handlers">{tab === "handlers" && <HandlersView />}</TabsContent>
  </Tabs>;
}
