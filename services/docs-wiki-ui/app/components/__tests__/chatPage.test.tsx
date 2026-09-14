import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { addRecent } from "../../lib/recentChats";
import ChatPage from "../../routes/chat";

// Mirrors appLayout.test.tsx's helper: Object.defineProperty on innerWidth
// isn't reverted by vi.unstubAllGlobals(), so it's captured/restored here too
// — otherwise a mobile test would leak innerWidth=375 into every later test
// in this file (useIsMobile reads window.innerWidth).
const ORIGINAL_INNER_WIDTH = window.innerWidth;
function stubMobileViewport() {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 375 });
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: ORIGINAL_INNER_WIDTH });
});

// Registers both route shapes chat.tsx can be reached by (bare "new
// conversation" and "existing conversation with :key"), same as
// app/routes.ts, so navigate() calls inside ChatPage resolve within this
// test tree exactly as they would in the real router.
function renderChatPage(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path=":app/chat" element={<ChatPage />} />
        <Route path=":app/chat/:key" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
}


test("switching to a different recent conversation reloads transcript in place", async () => {
  addRecent("shop", { key: "k1", firstQuestion: "first?", ts: 1 });
  addRecent("shop", { key: "k2", firstQuestion: "second?", ts: 2 });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([{ name: "shop", ask_enabled: true, ask_reason: null }]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify([]));
    if (u.endsWith("/api/shop/chats/k1")) {
      return new Response(JSON.stringify({
        session: "k1",
        messages: [{ role: "user", content: "first?" }, { role: "assistant", content: "First answer." }],
      }));
    }
    if (u.endsWith("/api/shop/chats/k2")) {
      return new Response(JSON.stringify({
        session: "k2",
        messages: [{ role: "user", content: "second?" }, { role: "assistant", content: "Second answer." }],
      }));
    }
    return new Response(JSON.stringify([]));
  }));

  renderChatPage("/shop/chat/k1");

  await waitFor(() => expect(screen.getByText("First answer.")).toBeInTheDocument());
  await userEvent.click(screen.getByRole("link", { name: "second?" }));
  await waitFor(() => expect(screen.getByText("Second answer.")).toBeInTheDocument());
  expect(screen.queryByText("First answer.")).toBeNull();
});

test("switching away before a slow transcript load resolves does not clobber the new view", async () => {
  addRecent("shop", { key: "k1", firstQuestion: "first?", ts: 1 });
  addRecent("shop", { key: "k2", firstQuestion: "second?", ts: 2 });

  // k1's transcript response is controlled manually so it can be resolved
  // AFTER the user has already navigated on to k2 — reproducing the race
  // where a slow getChat for an abandoned conversation lands late and must
  // not overwrite whatever is currently displayed.
  let resolveK1!: (r: Response) => void;
  const k1Response = new Promise<Response>((resolve) => { resolveK1 = resolve; });

  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([{ name: "shop", ask_enabled: true, ask_reason: null }]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify([]));
    if (u.endsWith("/api/shop/chats/k1")) return k1Response;
    if (u.endsWith("/api/shop/chats/k2")) {
      return new Response(JSON.stringify({
        session: "k2",
        messages: [{ role: "user", content: "second?" }, { role: "assistant", content: "Second answer." }],
      }));
    }
    return new Response(JSON.stringify([]));
  }));

  renderChatPage("/shop/chat/k1");
  // k1's fetch is in flight but deliberately not resolved yet.

  // The recent list is now server-fetched (falling back to localStorage
  // asynchronously), so the "second?" link is no longer present on the very
  // first synchronous render — findByRole waits for it the way the other
  // tests in this file already do via an initial waitFor.
  await userEvent.click(await screen.findByRole("link", { name: "second?" }));
  await waitFor(() => expect(screen.getByText("Second answer.")).toBeInTheDocument());

  // The slow k1 response finally lands, well after the user moved on to k2.
  resolveK1(new Response(JSON.stringify({
    session: "k1",
    messages: [{ role: "user", content: "first?" }, { role: "assistant", content: "First answer." }],
  })));
  await new Promise((r) => setTimeout(r, 20));

  expect(screen.queryByText("First answer.")).toBeNull();
  expect(screen.getByText("Second answer.")).toBeInTheDocument();
});



test("delete button removes a recent entry and calls DELETE, leaving the open conversation untouched", async () => {
  addRecent("shop", { key: "k1", firstQuestion: "first?", ts: 1 });
  addRecent("shop", { key: "k2", firstQuestion: "second?", ts: 2 });
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([{ name: "shop", ask_enabled: true, ask_reason: null }]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify([]));
    if (u.endsWith("/api/shop/chats/k1") && init?.method === "DELETE") {
      return new Response(null, { status: 204 });
    }
    if (u.endsWith("/api/shop/chats/k1")) {
      return new Response(JSON.stringify({
        session: "k1",
        messages: [{ role: "user", content: "first?" }, { role: "assistant", content: "First answer." }],
      }));
    }
    if (u.endsWith("/api/shop/chats/k2")) {
      return new Response(JSON.stringify({
        session: "k2",
        messages: [{ role: "user", content: "second?" }, { role: "assistant", content: "Second answer." }],
      }));
    }
    return new Response(JSON.stringify([]));
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChatPage("/shop/chat/k2");
  await waitFor(() => expect(screen.getByText("Second answer.")).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "first?" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "delete first?" }));
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));

  await waitFor(() => expect(screen.queryByRole("link", { name: "first?" })).toBeNull());
  const deleteCall = fetchMock.mock.calls.find(([u, i]) =>
    String(u).endsWith("/api/shop/chats/k1") && (i as RequestInit | undefined)?.method === "DELETE");
  expect(deleteCall).toBeDefined();
  // k2 (the currently-open conversation) was not deleted or navigated away from.
  expect(screen.getByText("Second answer.")).toBeInTheDocument();
});

test("deleting the currently-open conversation navigates back to /:app/chat", async () => {
  addRecent("shop", { key: "k1", firstQuestion: "first?", ts: 1 });
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([{ name: "shop", ask_enabled: true, ask_reason: null }]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify([]));
    if (u.endsWith("/api/shop/chats/k1") && init?.method === "DELETE") {
      return new Response(null, { status: 204 });
    }
    if (u.endsWith("/api/shop/chats/k1")) {
      return new Response(JSON.stringify({
        session: "k1",
        messages: [{ role: "user", content: "first?" }, { role: "assistant", content: "First answer." }],
      }));
    }
    return new Response(JSON.stringify([]));
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChatPage("/shop/chat/k1");
  await waitFor(() => expect(screen.getByText("First answer.")).toBeInTheDocument());

  await userEvent.click(screen.getByRole("button", { name: "delete first?" }));
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));

  await waitFor(() => expect(screen.queryByText("First answer.")).toBeNull());
  expect(screen.getByRole("link", { name: "New conversation" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "first?" })).toBeNull();
  const deleteCall = fetchMock.mock.calls.find(([u, i]) =>
    String(u).endsWith("/api/shop/chats/k1") && (i as RequestInit | undefined)?.method === "DELETE");
  expect(deleteCall).toBeDefined();
});

test("declining the delete dialog keeps the conversation", async () => {
  addRecent("shop", { key: "k1", firstQuestion: "first?", ts: 1 });
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    void init;
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([{ name: "shop", ask_enabled: true, ask_reason: null }]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify([]));
    return new Response(JSON.stringify([]));
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChatPage("/shop/chat");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "delete first?" })).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: "delete first?" }));
  await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));

  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(screen.getByRole("link", { name: "first?" })).toBeInTheDocument();
  const deleteCall = fetchMock.mock.calls.find(([, i]) =>
    (i as RequestInit | undefined)?.method === "DELETE");
  expect(deleteCall).toBeUndefined();
});

// Below `md` the fixed w-60 recents column is unusable (squeezes the chat
// panel), so it's replaced by a closed-by-default "Conversations (N)"
// Collapsible above the panel. Unlike DocView's TOC, this can't be a single
// CSS-only instance shared across breakpoints: the desktop aside must stay
// visible with no interaction (existing tests above assert "New
// conversation"/recent links with zero clicks), so the mobile drawer has to
// default closed as its own branch — `useIsMobile()` (already used by
// Sidebar, guarded matchMedia polyfill from vitest.setup.ts) picks exactly
// one of the two at render time, so there's still only ever one "New
// conversation" link / one delete button per entry in the DOM.
test("renders the server session list when identity yields one", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([{ name: "shop", ask_enabled: true, ask_reason: null }]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify([]));
    if (u.endsWith("/api/shop/chats")) {
      return new Response(JSON.stringify({
        sessions: [
          { session_key: "srv1", first_question: "server question one", questions: 2, first_ts: "t", last_ts: "t" },
        ],
      }));
    }
    return new Response(JSON.stringify([]));
  }));

  renderChatPage("/shop/chat");

  await waitFor(() => expect(screen.getByText("server question one")).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "server question one" }))
    .toHaveAttribute("href", "/shop/chat/srv1");
});

test("falls back to localStorage when the server list is empty", async () => {
  addRecent("shop", { key: "loc1", firstQuestion: "local question", ts: 1 });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([{ name: "shop", ask_enabled: true, ask_reason: null }]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify([]));
    if (u.endsWith("/api/shop/chats")) return new Response(JSON.stringify({ sessions: [] }));
    return new Response(JSON.stringify([]));
  }));

  renderChatPage("/shop/chat");

  await waitFor(() => expect(screen.getByText("local question")).toBeInTheDocument());
});

test("falls back to localStorage when the server list fetch fails", async () => {
  addRecent("shop", { key: "loc1", firstQuestion: "local question", ts: 1 });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([{ name: "shop", ask_enabled: true, ask_reason: null }]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify([]));
    // Distinct from the "empty sessions" fallback above: this is a hard
    // failure (server error) on the request itself, which getMyChats
    // surfaces as a thrown ApiError that loadRecent must also swallow.
    if (u.endsWith("/api/shop/chats")) {
      return new Response(JSON.stringify({ error: { message: "boom" } }), { status: 500 });
    }
    return new Response(JSON.stringify([]));
  }));

  renderChatPage("/shop/chat");

  await waitFor(() => expect(screen.getByText("local question")).toBeInTheDocument());
});

test("deleting refreshes the server list", async () => {
  let chatsCalls = 0;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([{ name: "shop", ask_enabled: true, ask_reason: null }]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify([]));
    if (u.endsWith("/api/shop/chats/srv1") && init?.method === "DELETE") {
      return new Response(null, { status: 204 });
    }
    if (u.endsWith("/api/shop/chats")) {
      chatsCalls += 1;
      if (chatsCalls === 1) {
        return new Response(JSON.stringify({
          sessions: [
            { session_key: "srv1", first_question: "server question one", questions: 2, first_ts: "t", last_ts: "t" },
          ],
        }));
      }
      return new Response(JSON.stringify({ sessions: [] }));
    }
    return new Response(JSON.stringify([]));
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChatPage("/shop/chat");
  await waitFor(() => expect(screen.getByText("server question one")).toBeInTheDocument());

  await userEvent.click(screen.getByRole("button", { name: "delete server question one" }));
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));

  await waitFor(() => expect(screen.queryByText("server question one")).toBeNull());
  const chatsListCalls = fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/api/shop/chats"));
  expect(chatsListCalls.length).toBe(2);
});


test("mobile viewport: conversations collapsible renders closed, a recent entry is reachable once opened", async () => {
  stubMobileViewport();
  addRecent("shop", { key: "k1", firstQuestion: "first?", ts: 1 });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.endsWith("/api/apps")) {
      return new Response(JSON.stringify([{ name: "shop", ask_enabled: true, ask_reason: null }]));
    }
    if (u.includes("/toc")) return new Response(JSON.stringify([]));
    return new Response(JSON.stringify([]));
  }));

  renderChatPage("/shop/chat");

  const trigger = await screen.findByRole("button", { name: "Conversations (1)" });
  expect(screen.queryByRole("link", { name: "first?" })).toBeNull();

  await userEvent.click(trigger);
  await waitFor(() => expect(screen.getByRole("link", { name: "first?" })).toBeInTheDocument());
});
