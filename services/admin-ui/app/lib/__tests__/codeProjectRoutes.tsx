import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";
import CodeFolderBrowser from "~/features/codeProjects/CodeFolderBrowser";
import CodeProjectLayout from "~/features/codeProjects/CodeProjectLayout";
import {
  CodeProjectDocumentsPage,
  CodeProjectIndex,
  CodeProjectOverviewPage,
  CodeProjectSettingsPage,
} from "~/features/codeProjects/CodeProjectPages";
import { CodeProjectDependenciesPage } from "~/features/codeProjects/CodeProjectDependencies";
import CodeProjectsLayout from "~/features/codeProjects/CodeProjectsLayout";
import CodeProjectsList from "~/features/codeProjects/CodeProjectsList";

/** The code projects routes as routes.ts declares them: the list's sub-pages and the project page's. */
export const codeProjectRoutes: RouteObject[] = [
  {
    path: "/code-projects",
    element: <CodeProjectsLayout />,
    children: [
      { index: true, element: <CodeProjectsList /> },
      { path: "folder", element: <CodeFolderBrowser /> },
    ],
  },
  {
    path: "/code-projects/:codeProjectId",
    element: <CodeProjectLayout />,
    children: [
      { index: true, element: <CodeProjectIndex /> },
      { path: "overview", element: <CodeProjectOverviewPage /> },
      { path: "dependencies", element: <CodeProjectDependenciesPage /> },
      { path: "documents", element: <CodeProjectDocumentsPage /> },
      { path: "settings", element: <CodeProjectSettingsPage /> },
    ],
  },
];

/** Renders the code projects routes (and `routes`) in a data router at `path`. It stubs the global Request: the test
 * file restores it with `vi.unstubAllGlobals()` in an afterEach. */
export function renderCodeProjects(path: string, routes: RouteObject[] = []) {
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
  const router = createMemoryRouter([...codeProjectRoutes, ...routes], { initialEntries: [path] });
  return { router, ...render(<RouterProvider router={router} />) };
}

/** One section link of a code projects page (`nav`: the navigation's name). */
export const sectionLink = (nav: string, name: string) =>
  within(screen.getByRole("navigation", { name: nav })).getByRole("link", { name });
