import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import routes from "~/routes";
import { renderRoutes } from "~/lib/__tests__/dataRouter";
import { eventsRoute } from "~/lib/__tests__/pageRoutes";

vi.mock("~/features/events/EventsView", () => ({ default: () => <p>The events list</p> }));
vi.mock("~/features/events/HandlersView", () => ({ default: () => <p>The handlers list</p> }));
afterEach(() => vi.unstubAllGlobals());

const section = (name: string) =>
  within(screen.getByRole("navigation", { name: "Events sections" })).getByRole("link", { name });

test("routes: Events is a parent route with Events (the index) and Handlers as sub-pages", () => {
  const page = routes.find((item) => item.path === "events")!;
  expect(page.file).toBe("./routes/events.tsx");
  expect(page.children?.map((item) => [item.index ?? false, item.path, item.file])).toEqual([
    [true, undefined, "./routes/eventsList.tsx"],
    [false, "handlers", "./routes/eventHandlers.tsx"],
  ]);
  expect(routes.some((item) => item.path === "events/handlers")).toBe(false);
});

test("Events and Handlers are section links, each with its help line", async () => {
  const user = userEvent.setup();
  const { router } = renderRoutes([eventsRoute], "/events");
  expect(await screen.findByText("The events list")).toBeInTheDocument();
  expect(section("Events")).toHaveAttribute("aria-current", "page");
  expect(screen.getByText("What happened at your sources, newest first, and what each event led to.")).toBeVisible();
  await user.click(section("Handlers"));
  await waitFor(() => expect(router.state.location.pathname).toBe("/events/handlers"));
  expect(await screen.findByText("The handlers list")).toBeInTheDocument();
  expect(section("Handlers")).toHaveAttribute("aria-current", "page");
  expect(section("Events")).not.toHaveAttribute("aria-current");
  expect(screen.getByText("Rules that turn a matching event into a new event or an agent's answer.")).toBeVisible();
});

test("an old /events/handlers?handler= link still opens the handler", async () => {
  const { router } = renderRoutes([eventsRoute], "/events/handlers?handler=h-1");
  expect(await screen.findByText("The handlers list")).toBeInTheDocument();
  expect(router.state.location.search).toBe("?handler=h-1");
});
