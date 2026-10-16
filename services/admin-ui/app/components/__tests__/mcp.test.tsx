import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import MCPServersView from "~/features/llm/MCPServersView";
import * as api from "~/features/llm/mcp";
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
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getMCPServers).mockResolvedValue({ servers: [connection] });
});
it("preserves a saved token on edit and requires confirmation to remove a connection", async () => {
  const user = userEvent.setup();
  vi.mocked(api.saveMCPServer).mockResolvedValue(connection);
  render(
    <MemoryRouter>
      <MCPServersView />
    </MemoryRouter>,
  );
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
  await user.click(screen.getByRole("button", { name: /^Remove$/ }));
  const dialog = screen.getByRole("alertdialog");
  expect(dialog).toHaveTextContent("removed from all agent configurations");
  expect(api.deleteMCPServer).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(api.deleteMCPServer).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: /^Remove$/ }));
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove MCP server" }));
  await waitFor(() => expect(api.deleteMCPServer).toHaveBeenCalledWith(connection));
});
it("tests a saved connection and shows discovered tools", async () => {
  const user = userEvent.setup();
  vi.mocked(api.testMCPServer).mockResolvedValue({
    ok: true,
    message: "Connected. Discovered 1 tools.",
    server: connection,
  });
  render(
    <MemoryRouter>
      <MCPServersView />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("button", { name: "Test connection" }));
  await screen.findByText("Connected. Discovered 1 tools.");
  expect(api.testMCPServer).toHaveBeenCalledWith(connection);
  await user.click(screen.getByText("1 tools · Connection verified"));
  expect(screen.getByText("read_info")).toBeVisible();
});
