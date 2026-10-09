import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import SchedulesView from "../SchedulesView";
import type { Schedule } from "../../lib/schedules";

const schedule: Schedule = { id: "s-1", name: "Jira auto pull", target_kind: "jira.pull", target_id: "jira-1", cron: "*/15 * * * *",
  description: "Every 15 minutes", timezone: "Europe/Belgrade", enabled: true, next_run_at: "2026-10-09T10:15:00Z", running: false,
  last_run_at: "2026-10-09T10:00:00Z", last_finished_at: "2026-10-09T10:00:02Z", last_status: "succeeded", last_message: "Pulled 25 of 140 tickets.", revision: 3 };

function api(routes: Record<string, (init?: RequestInit) => [number, unknown]>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const handler = routes[key];
    if (!handler) throw new Error(`unexpected ${key}`);
    const [status, body] = handler(init);
    return new Response(JSON.stringify(body), { status });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const body = (fetch: ReturnType<typeof api>, key: string) => JSON.parse(fetch.mock.calls.find(([url, init]) => `${init?.method ?? "GET"} ${url}` === key)![1]!.body as string);
const view = () => render(<MemoryRouter><SchedulesView /></MemoryRouter>);

afterEach(() => vi.unstubAllGlobals());

test("lists schedules with their timetable and last result", async () => {
  api({ "GET /api/schedules": () => [200, { schedules: [schedule] }] });
  view();
  const row = (await screen.findByText("Jira auto pull")).closest("tr")!;
  expect(within(row).getByText("Jira tickets")).toBeInTheDocument();
  expect(within(row).getByText("Every 15 minutes")).toBeInTheDocument();
  expect(within(row).getByText("Europe/Belgrade")).toBeInTheDocument();
  expect(within(row).getByText("Succeeded")).toBeInTheDocument();
  expect(within(row).getByText("Pulled 25 of 140 tickets.")).toBeInTheDocument();
});

test("shows the empty state", async () => {
  api({ "GET /api/schedules": () => [200, { schedules: [] }] });
  view();
  expect(await screen.findByText("No schedules yet. Turn on Auto pull on Jira → Tickets.")).toBeInTheDocument();
});

test("pausing sends the revision and shows the paused row", async () => {
  const user = userEvent.setup();
  const fetch = api({ "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "PUT /api/schedules/s-1": () => [200, { schedule: { ...schedule, enabled: false, next_run_at: null, revision: 4 } }] });
  view();
  await user.click(await screen.findByRole("switch", { name: "Enabled: Jira auto pull" }));
  expect(await screen.findByText("Paused")).toBeInTheDocument();
  expect(body(fetch, "PUT /api/schedules/s-1")).toEqual({ cron: "*/15 * * * *", timezone: "Europe/Belgrade", enabled: false, revision: 3 });
});

test("run now while running shows the server message", async () => {
  const user = userEvent.setup();
  api({ "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "POST /api/schedules/s-1/run": () => [409, { error: { message: "This schedule is already running." } }] });
  view();
  await user.click(await screen.findByRole("button", { name: "Run now" }));
  expect(await screen.findByText("This schedule is already running.")).toBeInTheDocument();
});

test("history lists the newest runs", async () => {
  const user = userEvent.setup();
  api({ "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "GET /api/schedules/s-1/runs": () => [200, { runs: [{ id: 2, trigger: "manual", started_at: "2026-10-09T10:00:00Z", finished_at: "2026-10-09T10:00:02Z", status: "failed", message: "Could not reach Jira." }] }] });
  view();
  await user.click(await screen.findByRole("button", { name: "History" }));
  expect(await screen.findByText(/Run now · Failed · Could not reach Jira\./)).toBeInTheDocument();
});

test("editor previews a custom cron, shows errors under the field and saves", async () => {
  const user = userEvent.setup();
  const fetch = api({ "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "POST /api/schedules/preview": (init) => JSON.parse(init!.body as string).cron === "*/2 * * * *"
      ? [400, { error: { message: "Schedules may run at most every 5 minutes." } }]
      : [200, { runs: ["2026-10-12T05:00:00Z", "2026-10-13T05:00:00Z", "2026-10-14T05:00:00Z"] }],
    "PUT /api/schedules/s-1": () => [200, { schedule: { ...schedule, cron: "0 7 * * 1-5", description: "0 7 * * 1-5", revision: 4 } }] });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("combobox", { name: "How often" }));
  await user.click(screen.getByRole("option", { name: "Custom" }));
  const cron = screen.getByLabelText("Cron expression");
  await user.clear(cron); await user.type(cron, "*/2 * * * *");
  expect(await screen.findByText("Schedules may run at most every 5 minutes.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  await user.clear(cron); await user.type(cron, "0 7 * * 1-5");
  expect(await screen.findByText("Next runs")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(body(fetch, "PUT /api/schedules/s-1")).toEqual({ cron: "0 7 * * 1-5", timezone: "Europe/Belgrade", enabled: true, revision: 3 }));
  expect(await screen.findByText("0 7 * * 1-5")).toBeInTheDocument();
});
