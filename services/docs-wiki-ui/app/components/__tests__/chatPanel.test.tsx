import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { listRecent } from "../../lib/recentChats";
import ChatPanel from "../ChatPanel";

// Markdown is mocked with a call-counting passthrough so tests can assert
// WHEN turn bodies re-render (flicker regression below) while still finding
// their text in the DOM.
const markdownSpy = vi.fn();
vi.mock("../Markdown", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../Markdown")>();
  const Actual = actual.default;
  return {
    default: (props: { content: string }) => {
      markdownSpy(props.content);
      return <Actual {...props} />;
    },
  };
});

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

function renderPanel(props: Partial<Parameters<typeof ChatPanel>[0]> = {}) {
  const onSession = vi.fn();
  render(
    <MemoryRouter>
      <ChatPanel app="shop" askEnabled={true} askReason={null} onSession={onSession} {...props} />
    </MemoryRouter>,
  );
  return onSession;
}

test("disabled state renders reason, no input", () => {
  renderPanel({ askEnabled: false, askReason: "Ask is not configured" });
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.getByText("Ask is not configured")).toBeInTheDocument();
});

test("loads an existing transcript when sessionKey given", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    session: "k9",
    messages: [{ role: "user", content: "first?" }, { role: "assistant", content: "First answer." }],
  }))));
  renderPanel({ sessionKey: "k9" });
  await waitFor(() => expect(screen.getByText("First answer.")).toBeInTheDocument());
  expect(screen.getByText("first?")).toBeInTheDocument();
});

test("question over 10000 chars is rejected client-side", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  renderPanel();
  const box = screen.getByRole("textbox");
  await userEvent.click(box);
  await userEvent.paste("x".repeat(10001));
  await userEvent.click(screen.getByRole("button", { name: /ask/i }));
  expect(screen.getByRole("alert")).toHaveTextContent(/10,000/);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("Shift+Enter inserts a newline instead of submitting", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  renderPanel();
  const box = screen.getByRole("textbox");
  await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
  expect(fetchMock).not.toHaveBeenCalled();
  expect(box).toHaveValue("line one\nline two");
});

test("stored NOT_COVERED transcripts render the friendly panel, never the sentinel", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    session: "k9",
    messages: [
      { role: "user", content: "do we use kafka?" },
      { role: "assistant", content: "NOT_COVERED" },
    ],
  }))));
  renderPanel({ sessionKey: "k9" });
  await waitFor(() =>
    expect(screen.getByText(/selected sources do not contain enough information/)).toBeInTheDocument());
  expect(screen.queryByText("NOT_COVERED")).toBeNull();
});

test("source selector renders the two available sources", () => {
  renderPanel({ sources: { docs: true, codex: true } });
  expect(screen.getByRole("checkbox", { name: "RAG" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Source code" })).toBeInTheDocument();
});

test("selector is hidden when docs is the only available source", () => {
  renderPanel({ sources: { docs: true, codex: false } });
  expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
});

test("selector is hidden when the sources capability is absent (old server)", () => {
  renderPanel();
  expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
});

test("restores a persisted selection from localStorage on mount", () => {
  localStorage.setItem("chat-sources:shop", JSON.stringify({ docs: true, codex: true }));
  renderPanel({ sources: { docs: true, codex: true } });
  expect(screen.getByRole("checkbox", { name: "RAG" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Source code" })).toBeChecked();
});

test("streamChat call omits sources entirely when the selector is hidden", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.endsWith("/api/shop/chat") && init?.method === "POST") {
      return new Response('TODO');
    }
    throw new Error(`unexpected fetch ${u}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  renderPanel();
  await userEvent.type(screen.getByRole("textbox"), "how do refunds work?");
  await userEvent.click(screen.getByRole("button", { name: /ask/i }));
  await waitFor(() => expect(screen.getByText("TODO")).toBeInTheDocument());
  const postCall = fetchMock.mock.calls.find(([u, i]) =>
    String(u).endsWith("/api/shop/chat") && (i as RequestInit | undefined)?.method === "POST");
  const body = JSON.parse((postCall![1] as RequestInit).body as string);
  expect(body).not.toHaveProperty("sources");
});

test("cannot uncheck the last remaining selected source", async () => {
  renderPanel({ sources: { docs: true, codex: true } });
  await userEvent.click(screen.getByRole("checkbox", { name: "Source code" }));
  expect(screen.getByRole("checkbox", { name: "RAG" })).toBeChecked();
  // Attempting to uncheck the last remaining checked source is a no-op —
  // the UI must prevent the request that would otherwise 400 server-side.
  await userEvent.click(screen.getByRole("checkbox", { name: "RAG" }));
  expect(screen.getByRole("checkbox", { name: "RAG" })).toBeChecked();
});

// Field report 2026-08-05: typing in the input re-rendered (and re-parsed)
// every prior turn's markdown on each keystroke — visible flicker/jank once
// transcripts carry heavy codex/graph-code answers. Completed turns must
// not re-render while the user types.
test("typing does not re-render completed assistant turns", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ session: 'k9', messages: [{ role: 'assistant', content: 'Answer one.' }] })));
  renderPanel({ sessionKey: 'k9' });
  await screen.findByText('Answer one.');
  const settled = markdownSpy.mock.calls.length;
  await userEvent.type(screen.getByRole("textbox"), "typing a new question now");
  expect(markdownSpy.mock.calls.length).toBe(settled);
});

test("historical assistant turns render sources_used badges", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    session: "k9",
    messages: [
      { role: "user", content: "how do refunds work?" },
      { role: "assistant", content: "Refunds are async.",
        sources_used: ["codex", "docs"] },
      { role: "user", content: "and cancellations?" },
      { role: "assistant", content: "Same flow." },
    ],
  }), { status: 200, headers: { "Content-Type": "application/json" } })));
  renderPanel({ sessionKey: "k9" });
  await waitFor(() => expect(screen.getByText("Refunds are async.")).toBeInTheDocument());
  expect(screen.getByText("Source code")).toBeInTheDocument();
  expect(screen.getByText("RAG")).toBeInTheDocument();
  // the tool-less turn renders no badges
  expect(screen.queryByText("docs")).toBeNull();
});

test("TODO preserves the question, ends loading and creates no session or recent history", async () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response('TODO', { headers: { 'Content-Type': 'text/plain' } }));
  vi.stubGlobal('fetch', fetchMock);
  const onSession = renderPanel();
  const user = userEvent.setup();
  const input = screen.getByRole('textbox');
  await user.type(input, 'Explain retries');
  await user.click(screen.getByRole('button', { name: 'Ask' }));
  expect(await screen.findByRole('status')).toHaveTextContent('TODO');
  expect(input).toHaveValue('Explain retries');
  expect(input).toBeEnabled();
  expect(onSession).not.toHaveBeenCalled();
  expect(listRecent('shop')).toEqual([]);
  await user.click(screen.getByRole('button', { name: 'Ask' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(screen.getAllByText('TODO')).toHaveLength(1);
  expect(JSON.parse(fetchMock.mock.calls[1][1]!.body as string)).toEqual({ question: 'Explain retries' });
});

test("TODO preserves an existing transcript and its advanced context", async () => {
  const context = { folders: ['payments'], include_subfolders: false, repositories: null };
  const fetchMock = vi.fn(async (url: string) => {
    if (url.endsWith('/context')) return Response.json({ folders: [{ path: 'payments', description: '', indexed_files: 1 }], repositories: [], warnings: [] });
    if (url.endsWith('/chats/saved')) return Response.json({ session: 'saved', messages: [{ role: 'assistant', content: 'Historical answer' }], context, sources: { docs: true, codex: false } });
    return new Response('TODO');
  });
  vi.stubGlobal('fetch', fetchMock);
  const onSession = renderPanel({ sessionKey: 'saved', contextEnabled: true, sources: { docs: true, codex: true } });
  expect(await screen.findByText('Historical answer')).toBeVisible();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Advanced' }));
  expect(screen.getByRole('checkbox', { name: 'payments' })).toBeChecked();
  expect(screen.getByRole('switch', { name: 'Search Git repositories' })).toBeDisabled();
  await user.type(screen.getByRole('textbox'), 'Follow up');
  await user.click(screen.getByRole('button', { name: 'Ask' }));
  expect(await screen.findByText('TODO')).toBeVisible();
  expect(screen.getByText('Historical answer')).toBeVisible();
  expect(onSession).not.toHaveBeenCalled();
});

test("failed submission shows the API error and can be retried", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(Response.json({ error: { message: 'Access denied' } }, { status: 403 })).mockResolvedValue(new Response('TODO'));
  vi.stubGlobal('fetch', fetchMock);
  renderPanel();
  const user = userEvent.setup();
  await user.type(screen.getByRole('textbox'), 'Explain');
  await user.click(screen.getByRole('button', { name: 'Ask' }));
  expect(await screen.findByText('Access denied')).toBeVisible();
  expect(screen.getByRole('textbox')).toBeEnabled();
  await user.type(screen.getByRole('textbox'), 'Try again');
  await user.keyboard('{Enter}');
  expect(await screen.findByText('TODO')).toBeVisible();
  expect(screen.queryByText('Access denied')).not.toBeInTheDocument();
});
