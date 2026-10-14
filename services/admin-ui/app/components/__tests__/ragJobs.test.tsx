import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import RagJobsView from "~/features/rag/RagJobsView";

const job = { id: "job1", status: "succeeded", input: { force: false }, result: { parsed_files: 3, embedded_chunks: 12 }, error: null,
  created_at: "2026-10-05T10:00:00Z", started_at: "2026-10-05T10:00:00Z", finished_at: "2026-10-05T10:01:05Z", collection: "jira", collection_id: "c1" };
const collections = { collections: [{ id: "c1", name: "jira" }, { id: "c2", name: "docs" }] };

function setup(url = "/rag/jobs", jobs: unknown[] = [job], total = jobs.length) {
  const fetch = vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.method === "POST") return Response.json({ id: "j2" });
    if (input.startsWith("/api/rag/collections")) return Response.json(collections);
    return Response.json({ jobs, total });
  });
  vi.stubGlobal("fetch", fetch);
  const router = createMemoryRouter([{ path: "/rag/jobs", element: <RagJobsView /> }], { initialEntries: [url] });
  render(<RouterProvider router={router} />);
  const jobCalls = () => fetch.mock.calls.map(([u]) => String(u)).filter((u) => u.startsWith("/api/rag/jobs"));
  return { fetch, router, jobCalls };
}

const NativeRequest = globalThis.Request;
// React Router's Node Request cannot consume jsdom's AbortSignal; fetch is mocked in this suite.
beforeEach(() => vi.stubGlobal("Request", class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
}));
afterEach(() => vi.unstubAllGlobals());

test("lists jobs with collection, status, mode, duration and result counts", async () => {
  setup();
  const row = (await screen.findByText("jira", { selector: "td *, td" })).closest("tr")!;
  expect(within(row).getByText("succeeded")).toBeInTheDocument();
  expect(within(row).getByText("Incremental")).toBeInTheDocument();
  expect(within(row).getByText("1m 5s")).toBeInTheDocument();
  expect(within(row).getByText(/parsed files/)).toBeInTheDocument();
});

test("the first request asks for page one with the default page size", async () => {
  const { jobCalls } = setup();
  await screen.findByText("Showing 1–1 of 1");
  expect(jobCalls()[0]).toBe("/api/rag/jobs?limit=25&offset=0");
});

test("filters and page come from the URL and are sent to the API", async () => {
  const { jobCalls } = setup("/rag/jobs?status=failed&collection=jira&q=token&page=3&size=10", [job], 42);
  await screen.findByText("Showing 21–30 of 42");
  const url = new URL(jobCalls()[0], "http://x");
  expect(Object.fromEntries(url.searchParams)).toEqual({ status: "failed", collection: "jira", q: "token", limit: "10", offset: "20" });
});

test("searching updates the URL, resets to page one and refetches", async () => {
  const user = userEvent.setup();
  const { router, jobCalls } = setup("/rag/jobs?page=2", [job], 60);
  await screen.findByText("Showing 26–50 of 60");
  await user.type(screen.getByRole("searchbox", { name: "Search jobs" }), "token");
  await waitFor(() => expect(router.state.location.search).toBe("?q=token"));
  await waitFor(() => expect(jobCalls().at(-1)).toBe("/api/rag/jobs?q=token&limit=25&offset=0"));
});

test("pagination moves between pages and disables at the ends", async () => {
  const user = userEvent.setup();
  const { router } = setup("/rag/jobs", [job], 60);
  await screen.findByText("Showing 1–25 of 60");
  expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Next page" }));
  await waitFor(() => expect(router.state.location.search).toBe("?page=2"));
  await screen.findByText("Showing 26–50 of 60");
  await user.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByText("Showing 51–60 of 60");
  expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
});

test("clear filters removes every filter from the URL", async () => {
  const user = userEvent.setup();
  const { router } = setup("/rag/jobs?status=failed&q=x&page=2");
  await user.click(await screen.findByRole("button", { name: "Clear filters" }));
  await waitFor(() => expect(router.state.location.search).toBe(""));
});

test("Run again syncs the collection by id; deleted collections cannot be rerun", async () => {
  const user = userEvent.setup();
  const { fetch } = setup("/rag/jobs", [job, { ...job, id: "job0", collection: "old", collection_id: null }]);
  await user.click(await screen.findByRole("button", { name: "Run again" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/rag/collections/c1/sync", expect.objectContaining({ method: "POST" })));
  expect(screen.getByText("Collection deleted")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Run again" })).toHaveLength(1);
});

test("active jobs can be cancelled and failed jobs show their error", async () => {
  const user = userEvent.setup();
  const { fetch } = setup("/rag/jobs", [{ ...job, id: "run1", status: "running" }, { ...job, id: "bad", status: "failed", error: "Embedding model rejected the token" }]);
  await user.click(await screen.findByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/rag/jobs/run1/cancel", expect.objectContaining({ method: "POST" })));
  expect(screen.getByText("Embedding model rejected the token")).toBeInTheDocument();
});

test("an empty result says so", async () => {
  setup("/rag/jobs?status=failed", [], 0);
  expect(await screen.findByText("No jobs match these filters.")).toBeInTheDocument();
});

test("automatic syncs show mode Auto", async () => {
  setup("/rag/jobs", [{ ...job, input: { auto: true } }]);
  const row = (await screen.findByText("jira", { selector: "td *, td" })).closest("tr")!;
  expect(within(row).getByText("Auto")).toBeInTheDocument();
});

test("an automatic sync that rebuilds still shows mode Auto", async () => {
  setup("/rag/jobs", [{ ...job, input: { auto: true, force: true } }]);
  const row = (await screen.findByText("jira", { selector: "td *, td" })).closest("tr")!;
  expect(within(row).getByText("Auto")).toBeInTheDocument();
  expect(within(row).queryByText("Force rebuild")).toBeNull();
});
