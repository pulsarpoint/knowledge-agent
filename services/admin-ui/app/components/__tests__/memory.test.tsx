import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import MemoryView from "~/features/memory/MemoryView";
import { Toaster } from "~/components/ui/sonner";
import { tooLongToCompare } from "~/features/memory/MemorySheet";

const entry = (name: string, extra = {}) => ({
  id: `id-${name}`,
  name,
  metadata: { service: "payments" },
  content: `Content of ${name}`,
  revision: 1,
  created_at: "2026-10-15T10:00:00Z",
  updated_at: "2026-10-15T10:00:00Z",
  created_by_agent: "coder",
  created_by_agent_name: "Coder",
  updated_by_agent: "coder",
  updated_by_agent_name: "Coder",
  updated_by_actor: null,
  updated_by_email: null,
  embedding_profile: null,
  ...extra,
});
const page = (memories: unknown[], extra = {}) => ({
  memories,
  next_offset: null,
  not_embedded: memories.length,
  embedding_model: true,
  current_profile: null,
  ...extra,
});

const settings = { embedding_model_id: null };
const llm = {
  models: [
    { id: "embed", name: "Document vectors", api_format: "embeddings", enabled: true },
    { id: "off", name: "Retired vectors", api_format: "embeddings", enabled: false },
    { id: "chat", name: "Chat model", api_format: "responses", enabled: true },
  ],
};

function api(handler: (url: string, init?: RequestInit) => unknown) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const body =
      url === "/api/memory/settings" && !init?.method ? settings : url === "/api/llm" ? llm : handler(url, init);
    if (body instanceof Response) return body;
    if (body === undefined) throw new Error(`unexpected ${init?.method ?? "GET"} ${url}`);
    return new Response(JSON.stringify(body));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const view = (url = "/memory") =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <MemoryView />
      <Toaster />
    </MemoryRouter>,
  );

afterEach(() => vi.unstubAllGlobals());

test("lists entries with who saved them, metadata and the not-embedded badge", async () => {
  api((url) => {
    if (url === "/api/memories/agents") return { agents: [{ id: "coder", name: "Coder" }] };
    if (url === "/api/memories") return page([entry("payments/deploy")]);
  });
  view();
  const row = (await screen.findByText("payments/deploy")).closest("tr")!;
  expect(within(row).getByText("Coder")).toBeInTheDocument();
  expect(within(row).getByText("service = payments")).toBeInTheDocument();
  expect(within(row).getByText("Not embedded")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Re-embed/ })).toBeInTheDocument();
});

test("the embedding model is chosen on the Memory page and never offers chat models", async () => {
  const user = userEvent.setup();
  const fetch = api((url, init) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url === "/api/memories") return page([]);
    if (url === "/api/memory/settings" && init?.method === "PUT") return JSON.parse(init.body as string);
  });
  view();
  await user.click(await screen.findByRole("combobox", { name: "Embedding model" }));
  expect(screen.queryByRole("option", { name: "Chat model" })).not.toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Retired vectors (disabled)" })).toHaveAttribute("aria-disabled", "true");
  await user.click(screen.getByRole("option", { name: "Document vectors" }));
  await waitFor(() =>
    expect(JSON.parse(fetch.mock.calls.find(([, init]) => init?.method === "PUT")![1]!.body as string)).toEqual({
      embedding_model_id: "embed",
    }),
  );
  expect(screen.getByRole("link", { name: "LLM Settings" })).toHaveAttribute("href", "/llm-settings");
});

test("searching and clicking a metadata badge filter the list", async () => {
  const fetch = api((url) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url.startsWith("/api/memories")) return page([entry("payments/deploy")]);
  });
  view();
  await screen.findByText("payments/deploy");
  await userEvent.type(screen.getByRole("searchbox", { name: "Search memory" }), "release");
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/memories?query=release", expect.anything()));
  // The search waits for typing to pause: "r", "re", … are never sent, and "release" once.
  const searches = () => fetch.mock.calls.map(([url]) => String(url)).filter((url) => url.includes("query="));
  await new Promise((resolve) => setTimeout(resolve, 500));
  expect(searches()).toEqual(["/api/memories?query=release"]);
  await userEvent.click(screen.getByRole("button", { name: "service = payments" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      `/api/memories?query=release&metadata=${encodeURIComponent('{"service":"payments"}')}`,
      expect.anything(),
    ),
  );
});

test("an entry link (?entry=id) opens that entry", async () => {
  api((url) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url === "/api/memories") return page([entry("a")]);
    if (url === "/api/memories/id-a") return { memory: entry("a"), versions: [] };
  });
  view("/memory?entry=id-a");
  expect(await screen.findByLabelText("Content")).toHaveValue("Content of a");
});

test("editing shows a conflict when the entry changed meanwhile", async () => {
  api((url, init) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url === "/api/memories") return page([entry("a")]);
    if (url === "/api/memories/id-a" && !init?.method) return { memory: entry("a"), versions: [] };
    if (url === "/api/memories/id-a" && init?.method === "PUT")
      return new Response(JSON.stringify({ error: { message: "This entry changed since you opened it." } }), {
        status: 409,
      });
  });
  view();
  await userEvent.click(await screen.findByText("a"));
  const content = await screen.findByLabelText("Content");
  await userEvent.clear(content);
  await userEvent.type(content, "New text");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("This entry changed since you opened it.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Load current" })).toBeInTheDocument();
});

test("history restores an earlier version", async () => {
  const fetch = api((url, init) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url === "/api/memories") return page([entry("a", { revision: 2 })]);
    if (url === "/api/memories/id-a" && !init?.method)
      return {
        memory: entry("a", { revision: 2, content: "New" }),
        versions: [
          {
            revision: 1,
            name: "a",
            metadata: {},
            content: "Old",
            by_agent_name: "Coder",
            by_email: null,
            changed_at: "2026-10-15T09:00:00Z",
          },
        ],
      };
    if (url === "/api/memories/id-a/restore") return { memory: entry("a", { revision: 3, content: "Old" }) };
  });
  view();
  await userEvent.click(await screen.findByText("a"));
  await userEvent.click(await screen.findByRole("tab", { name: "History" }));
  await userEvent.click(screen.getByRole("button", { name: "Restore revision 1" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/memories/id-a/restore",
      expect.objectContaining({ body: JSON.stringify({ revision: 1, current_revision: 2 }) }),
    ),
  );
  expect(await screen.findByText("Revision 1 of “a” restored.")).toBeInTheDocument();
});

test("delete asks for confirmation and sends the revision", async () => {
  const fetch = api((url, init) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url === "/api/memories") return page([entry("a")]);
    if (url === "/api/memories/id-a" && !init?.method) return { memory: entry("a"), versions: [] };
    if (url === "/api/memories/id-a?revision=1") return new Response(null, { status: 204 });
  });
  view();
  await userEvent.click(await screen.findByText("a"));
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await userEvent.click(await screen.findByRole("button", { name: "Delete entry" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith("/api/memories/id-a?revision=1", expect.objectContaining({ method: "DELETE" })),
  );
  expect(await screen.findByText("Entry “a” deleted.")).toBeInTheDocument();
});

test("a new entry is created from the sheet", async () => {
  const fetch = api((url, init) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url === "/api/memories" && !init?.method) return page([]);
    if (url === "/api/memories" && init?.method === "POST") return { memory: entry("new/one") };
  });
  view();
  await userEvent.click(await screen.findByRole("button", { name: "New entry" }));
  await userEvent.type(screen.getByLabelText("Name"), "new/one");
  await userEvent.type(screen.getByLabelText("Content"), "Text");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/memories",
      expect.objectContaining({ body: JSON.stringify({ name: "new/one", metadata: {}, content: "Text" }) }),
    ),
  );
  expect(await screen.findByText("Entry “new/one” saved.")).toBeInTheDocument();
});

test("a partial re-embed failure stays visible after the list reloads", async () => {
  api((url, init) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url === "/api/memories/re-embed" && init?.method === "POST") return { embedded: 1, failed: 2 };
    if (url === "/api/memories") return page([entry("a")]);
  });
  view();
  await userEvent.click(await screen.findByRole("button", { name: /Re-embed/ }));
  expect(await screen.findByText("2 entries could not be embedded.")).toBeInTheDocument();
  await screen.findByText("a");
  expect(screen.getByText("2 entries could not be embedded.")).toBeInTheDocument();
});

test("an entry embedded with another model's profile shows Not embedded, the current one does not", async () => {
  api((url) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url === "/api/memories")
      return page([entry("old", { embedding_profile: "old" }), entry("fresh", { embedding_profile: "new" })], {
        current_profile: "new",
        not_embedded: 1,
      });
  });
  view();
  const old = (await screen.findByText("old")).closest("tr")!;
  expect(within(old).getByText("Not embedded")).toBeInTheDocument();
  expect(within(screen.getByText("fresh").closest("tr")!).queryByText("Not embedded")).not.toBeInTheDocument();
});

test("a rename clash shows its message without Load current", async () => {
  api((url, init) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url === "/api/memories") return page([entry("a")]);
    if (url === "/api/memories/id-a" && !init?.method) return { memory: entry("a"), versions: [] };
    if (url === "/api/memories/id-a" && init?.method === "PUT")
      return new Response(JSON.stringify({ error: { message: "An entry named b already exists." } }), { status: 409 });
  });
  view();
  await userEvent.click(await screen.findByText("a"));
  await screen.findByLabelText("Content");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("An entry named b already exists.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Load current" })).not.toBeInTheDocument();
});

test("a stale restore offers Load current", async () => {
  api((url, init) => {
    if (url === "/api/memories/agents") return { agents: [] };
    if (url === "/api/memories") return page([entry("a", { revision: 2 })]);
    if (url === "/api/memories/id-a" && !init?.method)
      return {
        memory: entry("a", { revision: 2 }),
        versions: [
          {
            revision: 1,
            name: "a",
            metadata: {},
            content: "Old",
            by_agent_name: null,
            by_email: null,
            changed_at: "2026-10-15T09:00:00Z",
          },
        ],
      };
    if (url === "/api/memories/id-a/restore")
      return new Response(JSON.stringify({ error: { message: "This entry changed since you opened it." } }), {
        status: 409,
      });
  });
  view();
  await userEvent.click(await screen.findByText("a"));
  await userEvent.click(await screen.findByRole("tab", { name: "History" }));
  await userEvent.click(screen.getByRole("button", { name: "Restore revision 1" }));
  expect(await screen.findByRole("button", { name: "Load current" })).toBeInTheDocument();
});

test("the line diff is skipped for texts over 2,000 lines", () => {
  const long = Array.from({ length: 2001 }, (_, n) => `line ${n}`).join("\n");
  expect(tooLongToCompare(long, "short")).toBe(true);
  expect(tooLongToCompare("short", long)).toBe(true);
  expect(tooLongToCompare("a\nb", "a\nc")).toBe(false);
});

test("the memory list: Skeleton rows while loading, pages by the API's total, a filter goes back to page 1", async () => {
  const user = userEvent.setup();
  const fetch = api((url) => {
    if (url === "/api/memories/agents") return { agents: [{ id: "coder", name: "Coder" }] };
    if (url === "/api/memories") return page([entry("first")], { next_offset: 20, total: 25 });
    if (url === "/api/memories?offset=20") return page([entry("later")], { total: 25 });
    if (url === "/api/memories?agent=coder") return page([entry("by-coder")], { total: 1 });
  });
  view();
  expect(screen.getAllByTestId("skeleton-row").length).toBeGreaterThan(0);
  await screen.findByRole("button", { name: "first" });
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  expect(screen.getByText("Showing 1–20 of 25")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByRole("button", { name: "later" })).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Saved by agent" }));
  await user.click(screen.getByRole("option", { name: "Coder" }));
  expect(await screen.findByRole("button", { name: "by-coder" })).toBeInTheDocument();
  expect(screen.getByText("Page 1 of 1")).toBeInTheDocument();
  expect(fetch.mock.calls.map(([url]) => url)).not.toContain("/api/memories?agent=coder&offset=20");
});
