import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router";
import * as profiles from "~/features/agents/api";
import { renderAgentApp } from "~/lib/__tests__/agentRoutes";
import AdminLayout from "../AdminLayout";
import AgentsView from "~/features/agents/AgentsView";

vi.mock("~/features/agents/api", async () => ({
  ...(await vi.importActual("~/features/agents/api")),
  getAgent: vi.fn(),
  getAgentOptions: vi.fn(),
  saveAgent: vi.fn(),
}));
vi.mock("~/features/agents/AgentProfileSettings", () => ({
  default: ({ profile }: { profile: profiles.AgentProfile }) => <p>Details of {profile.name}</p>,
}));
vi.mock("~/features/agents/AgentPromptCard", () => ({ default: () => null }));
vi.mock("~/features/agents/AgentChatView", () => ({
  default: ({ agentId, available }: { agentId: string; available: boolean }) => (
    <p>
      Chat with {agentId} {available ? "on" : "off"}
    </p>
  ),
}));

const agent: profiles.AgentProfile = {
  id: "ops",
  name: "Operations",
  description: "Runs the servers.",
  when_to_use: "",
  enabled: true,
  expert: true,
  revision: 2,
  model_id: null,
  system_prompt: "",
  instructions: "",
  access: { resources: [] },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))),
  );
  vi.mocked(profiles.getAgent).mockImplementation(async () => structuredClone(agent));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({
    server: [{ type: "server", id: "s1", name: "web-1", description_short: "" }],
    service: [],
    environment: [],
    code_project: [],
    document: [],
    mcp_server: [],
  });
});
afterEach(() => vi.unstubAllGlobals());

function Where() {
  const { pathname, search } = useLocation();
  return <output aria-label="Location">{pathname + search}</output>;
}
const open = (path: string) =>
  renderAgentApp(path, {
    wrap: (outlet) => (
      <AdminLayout>
        {outlet}
        <Where />
      </AdminLayout>
    ),
    routes: [{ path: "/agents", element: <p>All agents list</p> }],
  });
const where = () => screen.getByLabelText("Location").textContent;
const section = (name: string) =>
  within(screen.getByRole("navigation", { name: "Agent sections" })).getByRole("link", { name });
const header = () => within(screen.getByRole("region", { name: "Agent details" }));

test.each([
  ["general", "Details of Operations"],
  ["resources", "web-1"],
  ["chat", "Chat with ops on"],
])("the %s sub-page renders under the agent's header", async (page, text) => {
  open(`/agents/ops/${page}`);
  expect(await screen.findByText(text)).toBeInTheDocument();
  expect(header().getByRole("heading", { name: "Operations", level: 2 })).toBeVisible();
  expect(header().getByText("Expert")).toBeVisible();
  expect(header().getByText("Runs the servers.")).toBeVisible(); // on every sub-page
  expect(header().getByRole("link", { name: "All agents" })).toHaveAttribute("href", "/agents");
  const nav = within(screen.getByRole("navigation", { name: "Agent sections" }));
  expect(nav.getAllByRole("link").map((link) => link.textContent)).toEqual(["General", "Resources", "Chat"]);
  expect(nav.getByRole("link", { current: "page" })).toHaveTextContent(page[0].toUpperCase() + page.slice(1));
  expect(screen.getByRole("heading", { name: "Agents", level: 1 })).toBeInTheDocument();
  expect(profiles.getAgent).toHaveBeenCalledTimes(1);
});

test("the agent's page opens General", async () => {
  open("/agents/ops");
  expect(await screen.findByText("Details of Operations")).toBeInTheDocument();
  expect(where()).toBe("/agents/ops/general");
});

test.each([
  ["?view=chat&session_id=r1", "/agents/ops/chat?session_id=r1"],
  ["?view=test&session_id=r1", "/agents/ops/chat?session_id=r1"],
  ["?view=resources", "/agents/ops/resources"],
  ["?view=general", "/agents/ops/general"],
  ["?view=tools", "/agents/ops/general"],
])("an old link %s redirects to its sub-page, keeping the rest of the query", async (query, target) => {
  open(`/agents/ops${query}`);
  await waitFor(() => expect(where()).toBe(target));
});

test("the sections keep the query (a chat's open run) when switching", async () => {
  const user = userEvent.setup();
  open("/agents/ops/chat?session_id=r1");
  await screen.findByText("Chat with ops on");
  await user.click(section("General"));
  expect(where()).toBe("/agents/ops/general?session_id=r1");
  await user.click(section("Chat"));
  expect(where()).toBe("/agents/ops/chat?session_id=r1");
});

test("leaving Resources with unsaved changes asks first: Stay keeps them, Leave goes", async () => {
  const user = userEvent.setup();
  open("/agents/ops/resources");
  await user.click(await screen.findByRole("switch", { name: "web-1" }));
  await user.click(section("Chat"));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("You have unsaved changes. Leave without saving?")).toBeVisible();
  await user.click(within(dialog).getByRole("button", { name: "Stay" }));
  expect(where()).toBe("/agents/ops/resources");
  expect(screen.getByRole("switch", { name: "web-1" })).toBeChecked();
  await user.click(header().getByRole("link", { name: "All agents" }));
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
  expect(await screen.findByText("All agents list")).toBeInTheDocument();
  expect(where()).toBe("/agents");
});

test("without unsaved changes the sections switch without asking", async () => {
  const user = userEvent.setup();
  open("/agents/ops/resources");
  await screen.findByRole("switch", { name: "web-1" });
  await user.click(section("Chat"));
  expect(await screen.findByText("Chat with ops on")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

test.each([
  ["?agent=ops&view=resources", "/agents/ops/resources"],
  ["?agent=ops&view=test&session_id=r1", "/agents/ops/chat?session_id=r1"],
  ["?agent=ops", "/agents/ops/general"],
])("an old All agents link %s opens the agent's sub-page", async (query, target) => {
  renderAgentApp(`/agents${query}`, {
    wrap: (outlet) => (
      <>
        {outlet}
        <Where />
      </>
    ),
    routes: [{ path: "/agents", element: <AgentsView /> }],
  });
  await waitFor(() => expect(where()).toBe(target));
});
