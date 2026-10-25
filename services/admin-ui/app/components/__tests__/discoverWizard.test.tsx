import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";
import DiscoverWizard from "~/features/sessions/DiscoverWizard";
import { options as shared } from "~/lib/__tests__/agentOptionsFixture";

const options = shared;

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
const NativeRequest = globalThis.Request;
beforeEach(() => {
  // the data router's navigations build Requests with a jsdom AbortSignal, which undici refuses
  vi.stubGlobal(
    "Request",
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined });
      }
    },
  );
  try {
    localStorage.clear();
  } catch {
    /* none */
  }
});
afterEach(() => vi.unstubAllGlobals());

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname + location.search}</output>;
}
function show(type: "server" | "service" | "environment" | "code_project", id: string) {
  const path = `/${type}s/${id}`;
  const router = createMemoryRouter(
    [
      {
        path: `/${type}s/:id`,
        element: (
          <>
            <DiscoverWizard resource={{ type, id, name: "web-1" }} />
            <Where />
          </>
        ),
      },
    ],
    { initialEntries: [path] },
  );
  render(<RouterProvider router={router} />);
}

const SERVER_DISCOVERY = "Explore the server in the Resource line and record what the inventory should hold about it.";
// The server's discovery prompts come apart from its chat prompts (options.discovery_prompts).
const withDiscovery = {
  ...options,
  discovery_prompts: [{ id: "p3", name: "Server discovery", content: SERVER_DISCOVERY }],
};

test("Discover… offers the library prompt, starts the run from it and opens the chat", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/sessions/options?server_id=s1": () => [200, withDiscovery],
    "POST /api/sessions": () => [201, { id: "r9" }],
  });
  show("server", "s1");
  expect(screen.queryByRole("button", { name: "Propose…" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Discover…" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).queryByRole("list", { name: "Steps" })).toBeNull();
  const prompt = (await within(dialog).findByRole("textbox", { name: "Prompt text" })) as HTMLTextAreaElement;
  expect(prompt.value).toBe(SERVER_DISCOVERY);
  expect(within(dialog).getByRole("combobox", { name: "Prompt" })).toHaveTextContent("Server discovery");
  await user.click(within(dialog).getByRole("combobox", { name: "Prompt" }));
  const choices = (await screen.findAllByRole("option")).map((item) => item.textContent);
  expect(choices).toEqual(["Server discovery", "Own prompt"]); // the discovery prompts, not the chat's
  await user.keyboard("{Escape}");
  await user.click(within(dialog).getByRole("button", { name: "Start" }));

  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/servers/s1?chat=r9"));
  const started = calls.find((call) => call.key === "POST /api/sessions")!.body;
  expect(started.started_from).toEqual({ server_id: "s1" });
  expect(started.prompt_id).toBe("p3");
  expect(started.system_prompt).toBe(SERVER_DISCOVERY);
  expect(started.message).toBe("Discover this server.");
  expect(started).not.toHaveProperty("tags");
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("the prompt text can be edited before the start", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/sessions/options?service_id=v1": () => [200, withDiscovery],
    "POST /api/sessions": () => [201, { id: "r7" }],
  });
  show("service", "v1");
  await user.click(screen.getByRole("button", { name: "Discover…" }));
  const dialog = await screen.findByRole("dialog");
  const prompt = (await within(dialog).findByRole("textbox", { name: "Prompt text" })) as HTMLTextAreaElement;
  await user.type(prompt, "\nBe brief.");
  await user.click(within(dialog).getByRole("button", { name: "Start" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r7"));
  const started = calls.find((call) => call.key === "POST /api/sessions")!.body;
  expect(started.started_from).toEqual({ service_id: "v1" });
  expect(started.system_prompt).toMatch(/Be brief\.$/);
});

test("an environment starts from the environment", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/sessions/options?environment_id=e1": () => [200, withDiscovery],
    "POST /api/sessions": () => [201, { id: "r5" }],
  });
  show("environment", "e1");
  await user.click(screen.getByRole("button", { name: "Discover…" }));
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByRole("textbox", { name: "Prompt text" });
  await user.click(within(dialog).getByRole("button", { name: "Start" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/environments/e1?chat=r5"));
  expect(calls.find((call) => call.key === "POST /api/sessions")!.body.started_from).toEqual({ environment_id: "e1" });
});

test("a blocked server cannot start and says why", async () => {
  const user = userEvent.setup();
  api({ "GET /api/sessions/options?server_id=s1": () => [200, withDiscovery] });
  const router = createMemoryRouter(
    [
      {
        path: "/servers/:id",
        element: (
          <DiscoverWizard resource={{ type: "server", id: "s1", name: "web-1" }} blockedReason="Set up SSH first." />
        ),
      },
    ],
    { initialEntries: ["/servers/s1"] },
  );
  render(<RouterProvider router={router} />);
  await user.click(screen.getByRole("button", { name: "Discover…" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("Set up SSH first.")).toBeInTheDocument();
  await within(dialog).findByRole("textbox", { name: "Prompt text" });
  expect(within(dialog).getByRole("button", { name: "Start" })).toBeDisabled();
});

test("a refused start shows why and keeps the sheet open", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/sessions/options?server_id=s1": () => [200, withDiscovery],
    "POST /api/sessions": () => [400, { error: { message: "SSH is not set up for this server." } }],
  });
  show("server", "s1");
  await user.click(screen.getByRole("button", { name: "Discover…" }));
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByRole("textbox", { name: "Prompt text" });
  await user.click(within(dialog).getByRole("button", { name: "Start" }));
  expect(await within(dialog).findByText("SSH is not set up for this server.")).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/servers\/s1$/);
});

test("on a code project Discover… offers Code project overview and starts from the code project", async () => {
  const user = userEvent.setup();
  const overview = { id: "p9", name: "Code project overview", content: "Read the repository." };
  const calls = api({
    "GET /api/sessions/options?code_project_id=c1": () => [200, { ...options, discovery_prompts: [overview] }],
    "POST /api/sessions": () => [201, { id: "r3" }],
  });
  show("code_project", "c1");
  await user.click(screen.getByRole("button", { name: "Discover…" }));
  const dialog = await screen.findByRole("dialog");
  const prompt = (await within(dialog).findByRole("textbox", { name: "Prompt text" })) as HTMLTextAreaElement;
  expect(prompt.value).toBe("Read the repository.");
  expect(within(dialog).getByText(/explores the code project/)).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Start" }));
  await waitFor(() => expect(calls.some((call) => call.key === "POST /api/sessions")).toBe(true));
  const started = calls.find((call) => call.key === "POST /api/sessions")!.body;
  expect(started).toMatchObject({ started_from: { code_project_id: "c1" }, prompt_id: "p9" });
  expect(started.message).toBe("Discover this code project.");
});
