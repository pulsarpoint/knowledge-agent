import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import GapsView from "../GapsView";

afterEach(() => vi.unstubAllGlobals());

test("gap entries are clickable: details collapsed until the entry is opened", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    gaps: [{ question: "what about taxes?", timestamp: "2026-08-03T10:00:00+00:00",
             near_misses: [{ path: "reference/billing.md", heading_path: "Billing > Endpoints" }] }],
    skipped: 2,
  }))));
  render(<GapsView app="shop" />);
  await waitFor(() => expect(screen.getByText("what about taxes?")).toBeInTheDocument());
  expect(screen.getByText(/2 malformed/)).toBeInTheDocument();
  // Collapsed: only the summary hint, not the near-miss details.
  expect(screen.getByText(/1 near miss/)).toBeInTheDocument();
  expect(screen.queryByText(/Billing > Endpoints/)).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: /what about taxes\?/ }));
  await waitFor(() => expect(screen.getByText(/Billing > Endpoints/)).toBeInTheDocument());
});

test("empty state", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ gaps: [], skipped: 0 }))));
  render(<GapsView app="shop" />);
  await waitFor(() => expect(screen.getByText(/No gaps recorded/)).toBeInTheDocument());
});

test("opening a gap renders the folded code answer as markdown with citations", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    gaps: [{
      question: "how do refunds get retried?", timestamp: "2026-08-04T10:00:00+00:00",
      near_misses: [],
      code_answer: {
        answer: "Refunds retry with backoff:\n\n```python\nRETRIES = 3\n```",
        citations: [{ file: "src/refunds/retry.py", lines: "5-9", quote: "retry up to 3 times" }],
        not_found: false,
        timestamp: "2026-08-04T10:05:00+00:00",
      },
    }],
    skipped: 0,
  }))));
  render(<GapsView app="shop" />);
  await waitFor(() => expect(screen.getByText("how do refunds get retried?")).toBeInTheDocument());
  // Collapsed: a "code answer" hint, no answer body yet.
  expect(screen.getByText(/code answer/)).toBeInTheDocument();
  expect(screen.queryByText(/Refunds retry with backoff/)).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: /how do refunds get retried\?/ }));
  await waitFor(() => expect(screen.getByText(/Refunds retry with backoff/)).toBeInTheDocument());
  // The fenced block renders as a real code element, not raw ``` text.
  const code = document.querySelector("pre code");
  expect(code?.textContent).toContain("RETRIES = 3");
  expect(screen.queryByText(/```/)).toBeNull();
  expect(screen.getByText("src/refunds/retry.py:5-9")).toBeInTheDocument();
});

test("malformed gap entry (missing/wrong-typed fields) renders without crashing", async () => {
  // The server only validates that a gaps-log line parses as a JSON object —
  // it doesn't guarantee any particular field shape, so the client has to
  // tolerate entries like these instead of throwing on `.length`/`.map`.
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    gaps: [
      { near_misses: "not-an-array", question: 42, timestamp: null },
      {},
    ],
    skipped: 0,
  }))));
  const { container } = render(<GapsView app="shop" />);
  await waitFor(() => expect(screen.getByText("Documentation gaps")).toBeInTheDocument());
  expect(container.querySelectorAll("li")).toHaveLength(2);
  // No "Closest: ..." line rendered for either malformed entry.
  expect(screen.queryByText(/Closest:/)).toBeNull();
});
