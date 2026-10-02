import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import AdminUsersView from "../AdminUsersView";
import AdminUserSessionsView from "../AdminUserSessionsView";
import AdminSessionView from "../AdminSessionView";

afterEach(() => vi.unstubAllGlobals());

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200,
    headers: { "Content-Type": "application/json" } });

test("users view lists users with counts and links", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ users: [
    { user_email: "a@x.io", sessions: 2, questions: 5,
      last_ts: "2026-08-10 12:00:00" },
  ]})));
  render(<MemoryRouter><AdminUsersView /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText("a@x.io")).toBeInTheDocument());
  expect(screen.getByText("5")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /a@x.io/ }))
    .toHaveAttribute("href", "/activity/user/a%40x.io");
});

test("sessions view lists a user's sessions linking to transcripts", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ sessions: [
    { session_key: "k1", first_question: "how do refunds work?",
      questions: 3, first_ts: "2026-08-10 11:00:00",
      last_ts: "2026-08-10 12:00:00" },
  ]})));
  render(<MemoryRouter>
    <AdminUserSessionsView email="a@x.io" />
  </MemoryRouter>);
  await waitFor(() =>
    expect(screen.getByText(/how do refunds work/)).toBeInTheDocument());
  expect(screen.getByRole("link", { name: /how do refunds work/ }))
    .toHaveAttribute("href", "/activity/session/k1");
});

test("transcript renders q/a turns with sources and a link to the agent session", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ rows: [
    { id: "r1", ts: "2026-10-02 11:00:00", user_email: "a@x.io", session_key: "s1", agent_id: "search",
      question: "q?", answer: "Refunds are async [1].", status: "completed", sources_used: ["docs"],
      sources: [{ n: 1, path: "payments/refunds.md", heading_path: "payments", anchor: null }],
      not_covered: true, escalated: false },
  ]})));
  render(<MemoryRouter><AdminSessionView sessionKey="s1" /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText("payments/refunds.md")).toBeInTheDocument());
  expect(screen.getByText("not covered")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open in agent session" }))
    .toHaveAttribute("href", "/agents/search?view=test&session_id=s1");
});

test("403 renders access-denied empty state", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(
    { error: { message: "admin access required" } }), { status: 403 })));
  render(<MemoryRouter><AdminUsersView /></MemoryRouter>);
  await waitFor(() =>
    expect(screen.getByText(/admin access required/i)).toBeInTheDocument());
});
