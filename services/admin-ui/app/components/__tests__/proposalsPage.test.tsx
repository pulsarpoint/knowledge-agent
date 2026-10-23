import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import ProposalsPage from "~/features/servers/ProposalsPage";

vi.mock("../Markdown", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

const proposal = (id: string, number: number, session: string, extra: Record<string, unknown> = {}) => ({
  id,
  kind: "document",
  number,
  status: "pending",
  revision: 1,
  session_id: session,
  server_id: null,
  result_id: null,
  decided_at: null,
  service_id: null,
  document_id: null,
  payload: { title: `Doc ${id}`, description: "", markdown: "# Doc", about_service: null },
  ...extra,
});
const accepted = proposal("q3", 1, "r1", {
  status: "accepted",
  result_id: "d7",
  draft_id: "d7",
  draft_published: false,
});
const published = proposal("q4", 2, "r1", {
  status: "accepted",
  result_id: "d8",
  draft_id: "d8",
  draft_published: true,
});
const runs = [
  {
    session_id: "r2",
    title: "Propose documents and more for this server.",
    started_at: "2026-10-14T12:00:00Z",
    started_by: "ana@example.com",
    proposals: [proposal("q1", 1, "r2"), proposal("q2", 2, "r2")],
  },
  {
    session_id: "r1",
    title: "Propose documents for this server.",
    started_at: "2026-10-13T12:00:00Z",
    started_by: null,
    proposals: [accepted, published],
  },
];

function api(routes: Record<string, (body: any) => [number, unknown]>) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${url}`;
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ key, body });
      if (!(key in routes)) throw new Error(`unexpected ${key}`);
      const [status, value] = routes[key](body);
      return new Response(JSON.stringify(value), { status });
    }),
  );
  return calls;
}
afterEach(() => vi.unstubAllGlobals());

const ALL = "GET /api/discovery/proposals?tag=servers%2Fs1";
const show = () =>
  render(
    <MemoryRouter>
      <ProposalsPage resource={{ type: "server", id: "s1" }} />
    </MemoryRouter>,
  );

test("proposals grouped by run, newest first, with who started it and a link to the chat", async () => {
  api({ [ALL]: () => [200, { runs }] });
  show();
  const groups = await screen.findAllByRole("region");
  expect(groups.map((group) => within(group).getByRole("heading").textContent)).toEqual([
    "Propose documents and more for this server.",
    "Propose documents for this server.",
  ]);
  expect(within(groups[0]).getByText(/ana@example.com/)).toBeInTheDocument();
  expect(within(groups[0]).getByRole("link", { name: "Open chat" })).toHaveAttribute("href", "/servers/s1?chat=r2");
  expect(within(groups[0]).getByText("1 · New document Doc q1")).toBeInTheDocument();
  expect(within(groups[0]).getByText("2 · New document Doc q2")).toBeInTheDocument();
  // accepted documents link to their draft, then to the live document
  expect(within(groups[1]).getByRole("link", { name: "Open draft" })).toHaveAttribute("href", "/documents/d7");
  expect(within(groups[1]).getByRole("link", { name: "Open document" })).toHaveAttribute("href", "/documents/d8");
});

test("the status filter asks for one status", async () => {
  const user = userEvent.setup();
  const calls = api({
    [ALL]: () => [200, { runs }],
    [`${ALL}&status=pending`]: () => [200, { runs: [{ ...runs[0] }] }],
    [`${ALL}&status=rejected`]: () => [200, { runs: [] }],
  });
  show();
  await screen.findAllByRole("region");
  const filter = screen.getByRole("group", { name: "Status" });
  expect(within(filter).getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
  await user.click(within(filter).getByRole("button", { name: "Pending" }));
  await waitFor(() => expect(screen.getAllByRole("region")).toHaveLength(1));
  expect(within(filter).getByRole("button", { name: "Pending" })).toHaveAttribute("aria-pressed", "true");
  await user.click(within(filter).getByRole("button", { name: "Rejected" }));
  expect(await screen.findByText("No rejected proposals.")).toBeInTheDocument();
  expect(calls.map((call) => call.key)).toEqual([ALL, `${ALL}&status=pending`, `${ALL}&status=rejected`]);
});

test("Accept and Reject call the existing routes and reload", async () => {
  const user = userEvent.setup();
  let decided = false;
  const calls = api({
    [ALL]: () => [
      200,
      {
        runs: decided
          ? [{ ...runs[0], proposals: [proposal("q1", 1, "r2"), { ...proposal("q2", 2, "r2"), status: "rejected" }] }]
          : runs,
      },
    ],
    "POST /api/discovery/proposals/q2/reject": () => {
      decided = true;
      return [200, { proposal: { ...proposal("q2", 2, "r2"), status: "rejected" } }];
    },
    "POST /api/discovery/proposals/q2/accept": () => [
      200,
      { proposal: { ...proposal("q2", 2, "r2"), status: "accepted" }, result_id: "d9" },
    ],
  });
  show();
  const [first] = await screen.findAllByRole("region");
  const item = within(first).getByText("2 · New document Doc q2").closest("li")!;
  await user.click(within(item as HTMLElement).getByRole("button", { name: "Review" }));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByRole("button", { name: "Accept" })).toBeInTheDocument();
  await user.click(within(sheet).getByRole("button", { name: "Reject" }));
  await waitFor(() => expect(calls.some((call) => call.key === "POST /api/discovery/proposals/q2/reject")).toBe(true));
  expect(calls.find((call) => call.key === "POST /api/discovery/proposals/q2/reject")!.body).toEqual({ revision: 1 });
  await waitFor(() => expect(calls.filter((call) => call.key === ALL)).toHaveLength(2));
});

test("no proposals yet", async () => {
  api({ [ALL]: () => [200, { runs: [] }] });
  show();
  expect(await screen.findByText(/No proposals yet/)).toBeInTheDocument();
});
