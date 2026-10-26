import type { ReactNode } from "react";
import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider, type RouteObject } from "react-router";
import AgentLayout from "~/features/agents/AgentLayout";
import { AgentChatPage, AgentGeneralPage, AgentIndex, AgentResourcesPage } from "~/features/agents/AgentPages";

/** The agent page routes as routes.ts declares them: a layout with its sub-pages. */
export const agentRoute: RouteObject = {
  path: "/agents/:agentId",
  element: <AgentLayout />,
  children: [
    { index: true, element: <AgentIndex /> },
    { path: "general", element: <AgentGeneralPage /> },
    { path: "resources", element: <AgentResourcesPage /> },
    { path: "chat", element: <AgentChatPage /> },
  ],
};

/** Renders the agent routes (and `routes`) in a data router at `path`, inside `wrap` (e.g. AdminLayout). It stubs the
 * global Request: the test file restores it with `vi.unstubAllGlobals()` in an afterEach. */
export function renderAgentApp(
  path: string,
  { routes = [], wrap = (outlet) => outlet }: { routes?: RouteObject[]; wrap?: (outlet: ReactNode) => ReactNode } = {},
) {
  // A data router's navigation builds a Request with jsdom's AbortSignal, which Node's Request refuses.
  const NativeRequest = globalThis.Request;
  vi.stubGlobal(
    "Request",
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(input, { ...init, signal: undefined });
      }
    },
  );
  const router = createMemoryRouter([{ element: wrap(<Outlet />), children: [agentRoute, ...routes] }], {
    initialEntries: [path],
  });
  return { router, ...render(<RouterProvider router={router} />) };
}

const nav = () => within(screen.getByRole("navigation", { name: "Agent sections" }));
/** The agent page's section links, by text. */
export const sectionNames = () =>
  nav()
    .getAllByRole("link")
    .map((link) => link.textContent);
/** One section link of the agent page. */
export const section = (name: string) => nav().getByRole("link", { name });
