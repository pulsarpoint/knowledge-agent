import { act, render, screen, waitFor, within } from "@testing-library/react";
import { renderRoutes } from "~/lib/__tests__/dataRouter";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MCPServersView from "~/features/llm/MCPServersView";
import * as api from "~/features/llm/mcp";
import { Toaster } from "~/components/ui/sonner";
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
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await act(() => router.navigate("/other"));
  expect(await screen.findByText("Other page")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await act(() => router.navigate("/mcp-servers"));
  await user.click(await screen.findByRole("button", { name: "Edit" }));
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
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Replace bearer token")).toHaveValue("");
  expect(screen.getByText(/Leave blank to keep it/)).toBeVisible();
  await user.type(screen.getByLabelText("Description"), " and health");
  await user.click(screen.getByRole("button", { name: "Save MCP server" }));
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
  await user.click(screen.getByRole("button", { name: /^Delete$/ }));
  const dialog = screen.getByRole("alertdialog");
  expect(dialog).toHaveTextContent("removed from all agent configurations");
  expect(within(dialog).getByRole("button", { name: "Delete MCP server" })).toHaveClass("bg-destructive");
  expect(api.deleteMCPServer).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(api.deleteMCPServer).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: /^Delete$/ }));
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
  await user.click(await screen.findByRole("button", { name: "Test connection" }));
  await screen.findByText("Connected. Discovered 1 tools.");
  expect(api.testMCPServer).toHaveBeenCalledWith(connection);
  await user.click(screen.getByText("1 tools · Connection verified"));
  expect(screen.getByText("read_info")).toBeVisible();
});
