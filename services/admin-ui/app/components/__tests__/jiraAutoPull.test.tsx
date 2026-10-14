import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import JiraAutoPullView from "~/features/jira/JiraAutoPullView";
import type { AutoPull, Schedule } from "~/features/events/schedules";

const schedule: Schedule = { id: "s-1", name: "Jira auto pull", target_kind: "jira.pull", target_id: "jira-1", cron: "*/15 * * * *",
  description: "Every 15 minutes", timezone: "UTC", enabled: true, next_run_at: "2026-10-09T10:15:00Z", running: false,
  last_run_at: "2026-10-09T10:00:00Z", last_finished_at: "2026-10-09T10:00:02Z", last_status: "succeeded", last_message: "Pulled 10 of 12 tickets.",
  revision: 1, params: { jql: "project = DEMO", limit: 10 } };

function api(state: AutoPull, saved?: AutoPull | [number, unknown]) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/schedules/preview") return new Response(JSON.stringify({ runs: ["2026-10-09T10:15:00Z"] }));
    if (url === "/api/schedules/s-1/run") return new Response(JSON.stringify({ schedule: { ...schedule, next_run_at: "2026-10-09T10:01:00Z" } }));
    if (url === "/api/jira/auto-pull" && init?.method === "PUT") {
      const [status, body] = Array.isArray(saved) ? saved : [200, saved];
      return new Response(JSON.stringify(body), { status });
    }
    return new Response(JSON.stringify(state));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const view = () => render(<MemoryRouter><JiraAutoPullView /></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

test("without a Jira connection it points to Settings", async () => {
  api({ schedule: null, jql: null, limit: null, connected: false });
  view();
  expect(await screen.findByText("Connect Jira first")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open Jira settings" })).toHaveAttribute("href", "/jira/settings");
});

test("saves its own query, limit and timetable", async () => {
  const user = userEvent.setup();
  const fetch = api({ schedule: null, jql: "ORDER BY updated DESC", limit: 25, connected: true },
    { schedule, jql: "project = DEMO", limit: 10, connected: true });
  view();
  const query = await screen.findByLabelText("Jira query (JQL)");
  await user.clear(query); await user.type(query, "project = DEMO");
  const count = screen.getByLabelText("Number of tickets");
  await user.clear(count); await user.type(count, "10");
  await user.click(screen.getByRole("switch", { name: "Pull tickets automatically" }));
  await screen.findByText("Next runs");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Auto pull saved.")).toBeInTheDocument();
  const put = fetch.mock.calls.find(([url, init]) => url === "/api/jira/auto-pull" && init?.method === "PUT")!;
  expect(JSON.parse(put[1]!.body as string)).toEqual({ enabled: true, cron: "*/15 * * * *", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", jql: "project = DEMO", limit: 10 });
  expect(screen.getByText(/Pulled 10 of 12 tickets\./)).toBeInTheDocument();
});

test("shows a server error and runs now", async () => {
  const user = userEvent.setup();
  api({ schedule, jql: "project = DEMO", limit: 10, connected: true }, [400, { error: { message: "Schedules may run at most every 5 minutes." } }]);
  view();
  await user.click(await screen.findByRole("button", { name: "Save" }));
  expect(await screen.findByText("Schedules may run at most every 5 minutes.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Run now" }));
  expect(await screen.findByText("The pull starts within a few seconds.")).toBeInTheDocument();
});
