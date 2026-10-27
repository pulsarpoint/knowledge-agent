import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";

/** Renders `routes` in a data router at `path` (a leave guard needs one) and returns the router. It stubs the global
 * Request: the test file restores it with `vi.unstubAllGlobals()` in an afterEach. */
export function renderRoutes(routes: RouteObject[], path: string) {
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
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return { router, ...render(<RouterProvider router={router} />) };
}
