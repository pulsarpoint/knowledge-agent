import { index, route, type RouteConfig } from "@react-router/dev/routes";

export default [
  index("./routes/home.tsx"),
  // Code projects: Projects and Code folder are sub-pages (folder is static, so it is never taken as a project id).
  route("code-projects", "./routes/codeProjects.tsx", [
    index("./routes/codeProjectsList.tsx"),
    route("folder", "./routes/codeFolder.tsx"),
  ]),
  // A code project's page holds its header; each section is a sub-page (an old ?view= link redirects from the index).
  // A resource page's chat is in its query (?chat=<id>): changing it keeps the page mounted.
  route("code-projects/:codeProjectId", "./routes/codeProject.tsx", [
    index("./routes/codeProjectIndex.tsx"),
    route("overview", "./routes/codeProjectOverview.tsx"),
    route("dependencies", "./routes/codeProjectDependencies.tsx"),
    route("documents", "./routes/codeProjectDocuments.tsx"),
    route("settings", "./routes/codeProjectSettings.tsx"),
  ]),
  route("code-graphs", "./routes/codeGraphs.tsx"),
  route("storage", "./routes/storage.tsx"),
  route("documents", "./routes/documents.tsx"),
  route("documents/new", "./routes/documentNew.tsx"),
  route("documents/drafts/:draftId", "./routes/documentDraft.tsx"),
  // The run link is a child of the document page: following one (or the pane changing run) keeps the page mounted.
  route("documents/:documentId", "./routes/documentDetail.tsx", [
    route("agent/:runId", "./routes/documentAgentRun.tsx"),
  ]),
  route("prompts", "./routes/promptLibrary.tsx"),
  route("prompts/new", "./routes/promptLibraryEditor.tsx", { id: "prompt-library-new" }),
  route("prompts/:promptId", "./routes/promptLibraryEditor.tsx"),
  route("sessions/:sessionId", "./routes/session.tsx"),
  route("documentation/folders", "./routes/documentationFolders.tsx"),
  route("documentation/folders/*", "./routes/documentationFolder.tsx"),
  route("services", "./routes/services.tsx"),
  route("services/new", "./routes/serviceNew.tsx"),
  // The service page holds its header; each tab is a sub-page (an old link or ?view= link redirects from the index).
  route("services/:serviceId", "./routes/service.tsx", [
    index("./routes/serviceIndex.tsx"),
    route("view", "./routes/serviceView.tsx"),
    route("edit", "./routes/serviceEdit.tsx"),
    route("code", "./routes/serviceCode.tsx"),
    route("depends-on", "./routes/serviceDependsOn.tsx"),
    route("used-by", "./routes/serviceUsedBy.tsx"),
    route("part-of", "./routes/servicePartOf.tsx"),
    route("parts", "./routes/serviceParts.tsx"),
    route("connections", "./routes/serviceConnections.tsx"),
    route("documents", "./routes/serviceDocuments.tsx"),
  ]),
  route("environments", "./routes/environments.tsx"),
  route("environments/:environmentId", "./routes/environment.tsx"),
  route("system-map", "./routes/systemMap.tsx"),
  // The agent page holds its header; each section is a sub-page (an old ?view= link redirects from the index).
  route("agents/:agentId", "./routes/agentDetail.tsx", [
    index("./routes/agentIndex.tsx"),
    route("general", "./routes/agentGeneral.tsx"),
    route("resources", "./routes/agentResources.tsx"),
    route("chat", "./routes/agentChat.tsx"),
  ]),
  route("agents", "./routes/agents.tsx"),
  route("output-schemas", "./routes/outputSchemas.tsx"),
  route("output-schemas/new", "./routes/outputSchemaEditor.tsx", { id: "output-schema-new" }),
  route("output-schemas/:schemaId", "./routes/outputSchemaEditor.tsx"),
  route("mcp-servers", "./routes/mcpServers.tsx"),
  route("skills", "./routes/skills.tsx"),
  route("skills/:skillId", "./routes/skill.tsx"),
  route("llm-settings", "./routes/llmSettings.tsx"),
  route("servers", "./routes/servers.tsx"),
  // A resource page's chat is in its query (?chat=<id>): changing it keeps the page mounted.
  route("servers/:serverId", "./routes/server.tsx"),
  route("jira/settings", "./routes/jiraSettings.tsx"),
  route("jira/tickets", "./routes/jiraTickets.tsx"),
  route("activity/runs", "./routes/runs.tsx"),
  route("activity/runs/:runKey", "./routes/runRecord.tsx"),
  route("schedules", "./routes/schedules.tsx"),
  route("events", "./routes/events.tsx"),
  route("events/handlers", "./routes/eventHandlers.tsx"),
  route("notifications", "./routes/notifications.tsx"),
  route("memory", "./routes/memory.tsx"),
] satisfies RouteConfig;
