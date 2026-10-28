import { act, render, screen, waitFor, within } from "@testing-library/react";
import { renderRoutes } from "~/lib/__tests__/dataRouter";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MCPServersView from "~/features/llm/MCPServersView";
import * as api from "~/features/llm/mcp";
import { Toaster } from "~/components/ui/sonner";
import { menuItems, rowAction, rowButtons } from "~/lib/__tests__/rowMenu";
vi.mock("~/features/llm/mcp");
vi.mock("../Markdown", () => ({ default: () => null }));
const connection: api.MCPServer = {
  id: "mcp-1",
  name: "Deployment tools",
  url: "https://mcp.test/mcp",
  description: "Read deployments",
  enabled: true,
  revision: 2,
  has_token: true,
  tools: [{ name: "read_info", description: "Read deployment information" }],
  last_tested_at: "2026-09-10T12:00:00Z",
  last_test_error: null,
};
/** The MCP servers page in a data router (its form's leave guard needs one), with a page to leave to. */
const page = () => {
  render(<Toaster />);
  return renderRoutes(
    [
      { path: "/mcp-servers", element: <MCPServersView /> },
      { path: "/other", element: <p>Other page</p> },
    ],
    "/mcp-servers",
  ).router;
};
afterEach(() => vi.unstubAllGlobals());
it("leaving with unsaved changes in the MCP server form asks Stay / Leave; an untouched form does not", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getMCPServers).mockResolvedValue({ servers: [connection] });
  const router = page();
  await rowAction(user, "Deployment tools", "Edit");
  await act(() => router.navigate("/other"));
  expect(await screen.findByText("Other page")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await act(() => router.navigate("/mcp-servers"));
  await rowAction(user, "Deployment tools", "Edit");
  await user.type(screen.getByLabelText("Name"), " 2");
  await act(() => router.navigate("/other"));
  const dialog = within(await screen.findByRole("alertdialog"));
  await user.click(dialog.getByRole("button", { name: "Stay" }));
  expect(router.state.location.pathname).toBe("/mcp-servers");
  await act(() => router.navigate("/other"));
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Leave" }));
  expect(await screen.findByText("Other page")).toBeInTheDocument();
});
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getMCPServers).mockResolvedValue({ servers: [connection] });
});
it("preserves a saved token on edit and requires confirmation to remove a connection", async () => {
  const user = userEvent.setup();
  vi.mocked(api.saveMCPServer).mockResolvedValue(connection);
  page();
  await rowAction(user, "Deployment tools", "Edit");
  expect(screen.getByLabelText("Replace bearer token")).toHaveValue("");
  expect(screen.getByText(/Leave blank to keep it/)).toBeVisible();
  await user.type(screen.getByLabelText("Description"), " and health");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(api.saveMCPServer).toHaveBeenCalledWith("mcp-1", {
      name: connection.name,
      description: "Read deployments and health",
      url: connection.url,
      enabled: true,
      revision: 2,
    }),
  );
  expect(
    await screen.findByText("MCP server “Deployment tools” saved. Test the connection to discover its tools."),
  ).toBeInTheDocument();
  await rowAction(user, "Deployment tools", "Delete");
  const dialog = screen.getByRole("alertdialog");
  expect(dialog).toHaveTextContent("removed from all agent configurations");
  expect(within(dialog).getByRole("button", { name: "Delete MCP server" })).toHaveClass("bg-destructive");
  expect(api.deleteMCPServer).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(api.deleteMCPServer).not.toHaveBeenCalled();
  await rowAction(user, "Deployment tools", "Delete");
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete MCP server" }));
  await waitFor(() => expect(api.deleteMCPServer).toHaveBeenCalledWith(connection));
  expect(await screen.findByText("MCP server “Deployment tools” deleted.")).toBeInTheDocument();
});
it("tests a saved connection and shows discovered tools", async () => {
  const user = userEvent.setup();
  vi.mocked(api.testMCPServer).mockResolvedValue({
    ok: true,
    message: "Connected. Discovered 1 tools.",
    server: connection,
  });
  page();
  await user.click(await screen.findByRole("button", { name: "Test connection for Deployment tools" }));
  await screen.findByText("Connected. Discovered 1 tools.");
  expect(api.testMCPServer).toHaveBeenCalledWith(connection);
  await user.click(screen.getByText("1 tools · Connection verified"));
  expect(screen.getByText("read_info")).toBeVisible();
});
it("an MCP server row keeps Test inline and Edit and Delete in its menu", async () => {
  const user = userEvent.setup();
  page();
  const row = (await screen.findByText("Deployment tools")).closest("tr")!;
  expect(rowButtons(row)).toEqual(["Test connection for Deployment tools", "Actions for Deployment tools"]);
  await user.click(within(row).getByRole("button", { name: "Actions for Deployment tools" }));
  expect(menuItems()).toEqual(["Edit", "Delete"]);
});
it("New MCP server opens a right sheet with Create MCP server and Cancel; Cancel with input asks Stay / Leave", async () => {
  const user = userEvent.setup();
  vi.mocked(api.saveMCPServer).mockResolvedValue(connection);
  page();
  await user.click(await screen.findByRole("button", { name: "New MCP server" }));
  const sheet = within(screen.getByRole("dialog", { name: "New MCP server" }));
  await user.click(sheet.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog", { name: "New MCP server" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "New MCP server" }));
  await user.type(screen.getByLabelText("Name"), "Search");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Stay" }));
  await user.type(screen.getByLabelText("HTTP endpoint"), "https://search.test/mcp");
  await user.click(screen.getByRole("button", { name: "Create MCP server" }));
  await waitFor(() =>
    expect(api.saveMCPServer).toHaveBeenCalledWith(null, {
      name: "Search",
      description: "",
      url: "https://search.test/mcp",
      enabled: true,
    }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "New MCP server" })).toBeNull());
  expect(screen.queryByRole("alertdialog")).toBeNull();
});
it("the MCP server list: Skeleton rows while loading, then an empty state with New MCP server", async () => {
  let resolve: (value: { servers: api.MCPServer[] }) => void = () => {};
  vi.mocked(api.getMCPServers).mockReturnValue(new Promise((done) => (resolve = done)));
  page();
  expect(await screen.findAllByTestId("skeleton-row")).not.toHaveLength(0);
  await act(async () => resolve({ servers: [] }));
  expect(await screen.findByText("No MCP servers yet")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "New MCP server" })).toHaveLength(2);
});
it("the MCP server list searches and sorts by a header", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getMCPServers).mockResolvedValue({
    servers: [connection, { ...connection, id: "m2", name: "Atlas", description: "Maps", enabled: false }],
  });
  page();
  await screen.findByText("Atlas");
  const names = () =>
    screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.querySelector("p.font-medium")?.textContent);
  expect(names()).toEqual(["Atlas", "Deployment tools"]);
  await user.click(screen.getByRole("button", { name: "Enabled" }));
  expect(names()).toEqual(["Deployment tools", "Atlas"]);
  await user.type(screen.getByRole("searchbox", { name: "Search MCP servers" }), "deploy");
  expect(names()).toEqual(["Deployment tools"]);
});
