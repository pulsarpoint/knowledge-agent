import type { RouteObject } from "react-router";
import ServerLayout, { ServerDocuments, ServerIndex, ServerOverview } from "~/features/servers/ServerPage";
import EnvironmentLayout, {
  EnvironmentDocuments,
  EnvironmentIndex,
  EnvironmentWorkspacePage,
} from "~/features/servers/EnvironmentPage";
import EventsLayout from "~/features/events/EventsPage";
import EventsView from "~/features/events/EventsView";
import HandlersView from "~/features/events/HandlersView";
import DocumentsLayout from "~/features/documents/DocumentsLayout";
import { DocumentDraftsList, DocumentsIndex } from "~/features/documents/DocumentsView";
import DocumentEditorView from "~/features/documents/DocumentEditorView";
import {
  DocumentAgentRunsPage,
  DocumentConnectionsPage,
  DocumentDraftsPage,
  DocumentHistoryPage,
} from "~/features/documents/DocumentPages";

/** The server page as routes.ts declares it: a layout with its sub-pages. */
export const serverRoute: RouteObject = {
  path: "/servers/:serverId",
  element: <ServerLayout />,
  children: [
    { index: true, element: <ServerIndex /> },
    { path: "overview", element: <ServerOverview /> },
    { path: "documents", element: <ServerDocuments /> },
  ],
};

/** The environment page as routes.ts declares it: a layout with its sub-pages. */
export const environmentRoute: RouteObject = {
  path: "/environments/:environmentId",
  element: <EnvironmentLayout />,
  children: [
    { index: true, element: <EnvironmentIndex /> },
    { path: "workspace", element: <EnvironmentWorkspacePage /> },
    { path: "documents", element: <EnvironmentDocuments /> },
  ],
};

/** Events as routes.ts declares it: a layout with Events (the index) and Handlers. */
export const eventsRoute: RouteObject = {
  path: "/events",
  element: <EventsLayout />,
  children: [
    { index: true, element: <EventsView /> },
    { path: "handlers", element: <HandlersView /> },
  ],
};

/** The Documents list as routes.ts declares it: a layout with Documents (the index) and Drafts. */
export const documentsRoute: RouteObject = {
  path: "/documents",
  element: <DocumentsLayout />,
  children: [
    { index: true, element: <DocumentsIndex /> },
    { path: "drafts", element: <DocumentDraftsList /> },
  ],
};

/** The document page as routes.ts declares it: the editor (its own Write page is the index) with its sub-pages; an old
 * run link (agent/:runId) redirects from the page. */
export const documentRoute: RouteObject = {
  path: "/documents/:documentId",
  element: <DocumentEditorView />,
  children: [
    { index: true, element: null },
    { path: "history", element: <DocumentHistoryPage /> },
    { path: "drafts", element: <DocumentDraftsPage /> },
    { path: "connections", element: <DocumentConnectionsPage /> },
    { path: "agent-runs", element: <DocumentAgentRunsPage /> },
    { path: "agent/:runId", element: null },
  ],
};
