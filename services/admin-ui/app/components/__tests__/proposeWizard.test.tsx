import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";
import ProposeWizard, { buildProposalPrompt, DEFAULT_PROPOSAL_OPTIONS } from "~/features/sessions/ProposeWizard";
import { options as shared } from "~/lib/__tests__/agentOptionsFixture";

// as the backend offers them for a resource: the proposal tools only a request turns on, none that edits a document
const options = {
  ...shared,
  tools: [
    ...shared.tools.filter((tool) => tool.needs !== "editable_document"),
    {
      name: "propose_service",
      label: "Propose service",
      summary: "Propose a service",
      group: "inventory",
      needs: "server_proposals",
      requires: "a server run whose request turns it on",
    },
    {
      name: "propose_document",
      label: "Propose document",
      summary: "Propose a document",
      group: "inventory",
      needs: "resource_proposals",
      requires: "a run whose request turns it on",
    },
  ],
};

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
function show(type: "server" | "service" | "environment", id: string) {
  const path = `/${type}s/${id}`;
  const router = createMemoryRouter(
    [
      {
        path: `/${type}s/:id`,
        element: (
          <>
            <ProposeWizard resource={{ type, id, name: "web-1" }} />
            <Where />
          </>
        ),
      },
    ],
    { initialEntries: [path] },
  );
  render(<RouterProvider router={router} />);
}

describe("buildProposalPrompt", () => {
  test("a server with everything on: the Server discovery steps, numbered, with the aspects", () => {
    const text = buildProposalPrompt("server", DEFAULT_PROPOSAL_OPTIONS);
    expect(text).toMatch(/^Explore the server in the Resource line and propose what the inventory should record/);
    expect(text).toContain(
      "1. Read what is already recorded first: get_resource for the server, then for its services.",
    );
    expect(text).toContain("Propose every service you find with propose_service");
    expect(text).toContain("A service that is already recorded is proposed as an update");
    expect(text).toContain("Propose one overview document for the server with propose_document");
    expect(text).toContain("Propose one document for each significant service (about_service)");
    expect(text).toContain("Cover in every document: configuration, logs, health checks, ports and paths.");
    expect(text).toMatch(/\d\. Finish with a short report/);
    expect(text).not.toContain("Extra instructions");
    const numbers = text.match(/^\d+\./gm)!.map((item) => Number(item.slice(0, -1)));
    expect(numbers).toEqual(numbers.map((_, index) => index + 1));
  });

  test("one block per chosen option", () => {
    const newOnly = buildProposalPrompt("server", { ...DEFAULT_PROPOSAL_OPTIONS, updateExisting: false });
    expect(newOnly).toContain("not recorded yet");
    expect(newOnly).toContain("Do not propose updates");
    const updateOnly = buildProposalPrompt("server", { ...DEFAULT_PROPOSAL_OPTIONS, findNew: false });
    expect(updateOnly).toContain("Do not propose new services.");
    const documentsOnly = buildProposalPrompt("server", {
      ...DEFAULT_PROPOSAL_OPTIONS,
      services: false,
      perService: false,
      aspects: ["logs"],
      extra: "  Mention the backup job.  ",
    });
    expect(documentsOnly).not.toContain("propose_service");
    expect(documentsOnly).not.toContain("about_service");
    expect(documentsOnly).toContain("Cover in every document: logs.");
    expect(documentsOnly).toMatch(/Extra instructions:\nMention the backup job\.$/);
    const servicesOnly = buildProposalPrompt("server", { ...DEFAULT_PROPOSAL_OPTIONS, documents: false });
    expect(servicesOnly).not.toContain("propose_document");
    expect(servicesOnly).not.toContain("Cover in every document");
  });

  test("a service or environment: documents only, read through get_resource", () => {
    const service = buildProposalPrompt("service", DEFAULT_PROPOSAL_OPTIONS);
    expect(service).toMatch(/^Explore the service in the Resource line/);
    expect(service).toContain("get_resource for the service, then for the servers it is deployed on");
    expect(service).not.toContain("propose_service");
    expect(service).not.toContain("about_service"); // one document per service: servers and environments only
    expect(service).toContain("Propose one overview document for the service with propose_document");
    const environment = buildProposalPrompt("environment", DEFAULT_PROPOSAL_OPTIONS);
    expect(environment).toContain("get_resource for the environment, then for its services and servers");
    expect(environment).toContain("about_service");
    expect(environment).not.toContain("propose_service");
  });
});

test("a server offers services and documents; the options; Review shows the prompt and tools; Start opens the chat", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/sessions/options?server_id=s1": () => [200, options],
    "POST /api/sessions": () => [201, { id: "r9" }],
  });
  show("server", "s1");
  await user.click(screen.getByRole("button", { name: "Propose…" }));
  const dialog = await screen.findByRole("dialog");
  const steps = within(dialog).getByRole("list", { name: "Steps" });
  expect(within(steps).getByText("What")).toHaveAttribute("aria-current", "step");
  expect(within(steps).getByText("Options")).toBeInTheDocument();
  expect(within(steps).getByText("Review")).toBeInTheDocument();
  expect(within(dialog).getByRole("checkbox", { name: "Services on this server" })).toBeChecked();
  expect(within(dialog).getByRole("checkbox", { name: "Documents" })).toBeChecked();
  await user.click(within(dialog).getByRole("button", { name: "Next" }));

  expect(within(steps).getByText("Options")).toHaveAttribute("aria-current", "step");
  for (const name of [
    "Update the services already recorded",
    "Look for new services",
    "An overview document of the server",
    "One document per service",
    "Configuration",
    "Logs",
    "Health checks",
    "Ports and paths",
  ])
    expect(within(dialog).getByRole("checkbox", { name })).toBeChecked();
  await user.click(within(dialog).getByRole("checkbox", { name: "Logs" }));
  await user.type(within(dialog).getByRole("textbox", { name: "Extra instructions" }), "Check the backups.");
  await user.click(within(dialog).getByRole("button", { name: "Next" }));

  expect(within(steps).getByText("Review")).toHaveAttribute("aria-current", "step");
  const prompt = within(dialog).getByRole("textbox", { name: "Prompt" }) as HTMLTextAreaElement;
  expect(prompt.value).toContain("Cover in every document: configuration, health checks, ports and paths.");
  expect(prompt.value).toMatch(/Extra instructions:\nCheck the backups\.$/);
  const tools = within(dialog).getByRole("list", { name: "Tools" });
  expect(within(tools).getByText("propose_service")).toBeInTheDocument();
  expect(within(tools).getByText("propose_document")).toBeInTheDocument();
  expect(within(tools).getByText("get_resource")).toBeInTheDocument();
  await user.type(prompt, "\nBe brief.");
  await user.click(within(dialog).getByRole("button", { name: "Start" }));

  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/servers/s1?chat=r9"));
  const started = calls.find((call) => call.key === "POST /api/sessions")!.body;
  expect(started.started_from).toEqual({ server_id: "s1" });
  expect(started.tags).toEqual(["proposals"]);
  expect(started.prompt_id).toBeNull();
  expect(started.system_prompt).toBe(prompt.value.trim());
  expect(started.system_prompt).toMatch(/Be brief\.$/);
  expect(started.tools).toEqual(expect.arrayContaining(["propose_service", "propose_document"]));
  expect(started.message).toBe("Propose services and documents for this server.");
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("only the chosen proposal tools are on: documents only", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/sessions/options?server_id=s1": () => [200, options],
    "POST /api/sessions": () => [201, { id: "r9" }],
  });
  show("server", "s1");
  await user.click(screen.getByRole("button", { name: "Propose…" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("checkbox", { name: "Services on this server" }));
  await user.click(within(dialog).getByRole("button", { name: "Next" }));
  expect(within(dialog).queryByRole("checkbox", { name: "Look for new services" })).toBeNull();
  await user.click(within(dialog).getByRole("button", { name: "Next" }));
  const tools = within(dialog).getByRole("list", { name: "Tools" });
  expect(within(tools).queryByText("propose_service")).toBeNull();
  expect(within(tools).getByText("propose_document")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Start" }));
  await waitFor(() => expect(calls.some((call) => call.key === "POST /api/sessions")).toBe(true));
  const started = calls.find((call) => call.key === "POST /api/sessions")!.body;
  expect(started.tools).toContain("propose_document");
  expect(started.tools).not.toContain("propose_service");
  expect(started.message).toBe("Propose documents for this server.");
});

test("Next needs something chosen", async () => {
  const user = userEvent.setup();
  api({ "GET /api/sessions/options?server_id=s1": () => [200, options] });
  show("server", "s1");
  await user.click(screen.getByRole("button", { name: "Propose…" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("checkbox", { name: "Services on this server" }));
  await user.click(within(dialog).getByRole("checkbox", { name: "Documents" }));
  expect(within(dialog).getByRole("button", { name: "Next" })).toBeDisabled();
});

test("a service offers documents only and starts from the service", async () => {
  const user = userEvent.setup();
  const calls = api({
    "GET /api/sessions/options?service_id=v1": () => [200, options],
    "POST /api/sessions": () => [201, { id: "r7" }],
  });
  show("service", "v1");
  await user.click(screen.getByRole("button", { name: "Propose…" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).queryByRole("checkbox", { name: "Services on this server" })).toBeNull();
  expect(within(dialog).getByRole("checkbox", { name: "Documents" })).toBeChecked();
  await user.click(within(dialog).getByRole("button", { name: "Next" }));
  expect(within(dialog).queryByRole("checkbox", { name: "One document per service" })).toBeNull();
  expect(within(dialog).getByRole("checkbox", { name: "An overview document of the service" })).toBeChecked();
  await user.click(within(dialog).getByRole("button", { name: "Next" }));
  const tools = within(dialog).getByRole("list", { name: "Tools" });
  expect(within(tools).queryByText("propose_service")).toBeNull();
  await user.click(within(dialog).getByRole("button", { name: "Start" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/services/v1?chat=r7"));
  const started = calls.find((call) => call.key === "POST /api/sessions")!.body;
  expect(started.started_from).toEqual({ service_id: "v1" });
  expect(started.tools).toContain("propose_document");
  expect(started.tools).not.toContain("propose_service");
  expect(started.tags).toEqual(["proposals"]);
});

test("an environment offers documents only, with one document per service", async () => {
  const user = userEvent.setup();
  api({ "GET /api/sessions/options?environment_id=e1": () => [200, options] });
  show("environment", "e1");
  await user.click(screen.getByRole("button", { name: "Propose…" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).queryByRole("checkbox", { name: "Services on this server" })).toBeNull();
  await user.click(within(dialog).getByRole("button", { name: "Next" }));
  expect(within(dialog).getByRole("checkbox", { name: "One document per service" })).toBeChecked();
});

test("a refused start shows why and keeps the wizard open", async () => {
  const user = userEvent.setup();
  api({
    "GET /api/sessions/options?server_id=s1": () => [200, options],
    "POST /api/sessions": () => [400, { error: { message: "SSH is not set up for this server." } }],
  });
  show("server", "s1");
  await user.click(screen.getByRole("button", { name: "Propose…" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Next" }));
  await user.click(within(dialog).getByRole("button", { name: "Next" }));
  await user.click(within(dialog).getByRole("button", { name: "Start" }));
  expect(await within(dialog).findByText("SSH is not set up for this server.")).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/servers\/s1$/);
});
