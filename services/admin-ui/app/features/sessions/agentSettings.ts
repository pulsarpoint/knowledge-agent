import { useEffect, useState } from "react";
import { getTools } from "~/features/agents/api";
import { choiceLabel, type OutputSchemaChoice } from "~/features/outputSchemas/api";
import { AGENT_KINDS, emptyIds } from "~/lib/resources";
import {
  getSessionOptions,
  isResourceScope,
  type AgentOptions,
  type AgentResources,
  type ChatScope,
  type ResourceType,
  type SessionDetail,
  type StartInput,
} from "./api";

/** The prompt choice for "my own text, no library prompt". */
export const OWN = "own";
/** Where a scope's settings are remembered in this browser: per document, per saved agent, per server, service or
 * environment, one for all writing with an agent. */
const STORAGE = (scope: ChatScope) =>
  scope.kind === "document"
    ? `ask-agent-settings:${scope.id}`
    : scope.kind === "new-document"
      ? "agent-settings:new-document"
      : `agent-settings:${scope.kind}:${scope.id}`;
const EMPTY: AgentResources = emptyIds(AGENT_KINDS);

/** What the next Ask agent run on a document starts with. */
export interface AgentSettings {
  modelId: string;
  promptKey: string;
  systemPrompt: string;
  chosen: AgentResources;
  /** A saved agent's run: the documents of its access picked for it (each as the agent has it). */
  documents?: string[];
  experts: string[];
  /** Catalog tools this run starts without (a saved agent's own off tools are added by the API). */
  disabledTools: string[];
  /** A saved agent's run: the output schema it finishes with (absent or null: a normal answer). */
  outputSchema?: OutputSchemaChoice | null;
}

const usable = (item: { id: string; usable?: boolean }) => item.usable !== false;
/** What a run may choose of a kind: a saved agent's run any resource of its access (a server without usable SSH is
 * still given), another run the usable ones. */
const choosable = (options: AgentOptions, key: (typeof AGENT_KINDS)[number]["key"]) =>
  options.agent ? options.resources[key] : options.resources[key].filter(usable);
const linkedIds = (options: AgentOptions) =>
  AGENT_KINDS.flatMap(({ key }) => options.resources[key].filter((item) => item.linked).map((item) => item.id)).sort();
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
/** An output schema choice as saved, or null when it is not one. */
function schemaChoice(value: unknown): OutputSchemaChoice | null {
  if (!value || typeof value !== "object") return null;
  const { id, name, version } = value as Record<string, unknown>;
  if (typeof id !== "string" || typeof name !== "string") return null;
  if (version !== null && !(typeof version === "number" && Number.isInteger(version) && version > 0)) return null;
  return { id, name, version };
}

export function defaultSettings(options: AgentOptions): AgentSettings {
  if (options.agent) {
    // a saved agent: its own model and prompt; everything its access holds is chosen (a run may pick a subset)
    const chosen = { ...EMPTY };
    for (const { key, field } of AGENT_KINDS) chosen[field] = options.resources[key].map((item) => item.id);
    return {
      modelId: options.agent.model?.id ?? options.default_model_id ?? "",
      promptKey: OWN,
      systemPrompt: options.agent.system_prompt,
      chosen,
      documents: options.resources.documents.map((item) => item.id),
      experts: options.experts.map((item) => item.id),
      disabledTools: [],
    };
  }
  const first = options.prompts.find((item) => item.id === options.default_prompt_id) ?? options.prompts[0];
  const chosen = { ...EMPTY };
  for (const { key, field } of AGENT_KINDS)
    chosen[field] = options.resources[key].filter((item) => item.linked && usable(item)).map((item) => item.id);
  return {
    modelId: options.default_model_id ?? options.models[0]?.id ?? "",
    promptKey: first ? first.id : OWN,
    systemPrompt: first ? first.content : "",
    chosen,
    experts: [],
    disabledTools: [],
  };
}

/** Saved settings checked against today's options: ids that no longer exist (and tools no longer in the catalog, `toolNames`) are dropped; resources start again from the
 * document's links when those changed since the save. Anything unreadable gives the defaults. */
export function restoreSettings(options: AgentOptions, saved: unknown, toolNames?: string[]): AgentSettings {
  const base = defaultSettings(options);
  if (!saved || typeof saved !== "object") return base;
  const { v, revision, linked, settings } = saved as {
    v?: unknown;
    revision?: unknown;
    linked?: unknown;
    settings?: Record<string, unknown>;
  };
  if (v !== 2 || !settings || typeof settings !== "object") return base;
  if (options.agent && revision !== options.agent.revision) return base; // the agent changed since the save
  const models = new Set(options.models.map((item) => item.id));
  const prompts = new Set(options.prompts.map((item) => item.id));
  const experts = new Set(options.experts.map((item) => item.id));
  const sameLinks = JSON.stringify(strings(linked).sort()) === JSON.stringify(linkedIds(options));
  const chosen = { ...base.chosen };
  if (sameLinks && settings.chosen && typeof settings.chosen === "object") {
    const savedChosen = settings.chosen as Record<string, unknown>;
    for (const { key, field } of AGENT_KINDS) {
      const present = new Set(choosable(options, key).map((item) => item.id));
      chosen[field] = strings(savedChosen[field]).filter((id) => present.has(id));
    }
  }
  const documents = new Set(options.resources.documents.map((item) => item.id));
  const modelId =
    typeof settings.modelId === "string" && models.has(settings.modelId) ? settings.modelId : base.modelId;
  const promptKey =
    typeof settings.promptKey === "string" && (settings.promptKey === OWN || prompts.has(settings.promptKey))
      ? settings.promptKey
      : base.promptKey;
  const systemPrompt =
    typeof settings.systemPrompt === "string" && settings.systemPrompt.trim()
      ? settings.systemPrompt
      : base.systemPrompt;
  const outputSchema = options.agent ? schemaChoice(settings.outputSchema) : null;
  return {
    modelId,
    promptKey,
    systemPrompt,
    chosen,
    ...(options.agent ? { documents: strings(settings.documents).filter((id) => documents.has(id)) } : {}),
    experts: strings(settings.experts).filter((id) => experts.has(id)),
    disabledTools: strings(settings.disabledTools).filter((name) => !toolNames || toolNames.includes(name)),
    ...(outputSchema ? { outputSchema } : {}),
  };
}

/** The settings a run started with, from its saved configuration (for Start new chat with these settings), checked
 * against today's options like restored settings: resources that are gone or not usable, experts that are gone and a
 * model that is gone are left out (the default model instead). */
export function settingsFromSession(options: AgentOptions, session: SessionDetail): AgentSettings {
  const config = (session.config ?? {}) as {
    experts?: { id: string }[];
    output_schema?: { follows_current?: unknown };
  };
  const pinned = options.agent ? schemaChoice(config.output_schema) : null;
  // a run started with Current pinned the version it found; it starts again with Current
  const outputSchema = pinned && config.output_schema?.follows_current === true ? { ...pinned, version: null } : pinned;
  const experts = new Set(options.experts.map((item) => item.id));
  // what the run was given (an environment, not what it brought)
  const given = session.given_resources ?? [];
  const chosen = { ...EMPTY };
  for (const { key, field } of AGENT_KINDS) {
    const present = new Set(choosable(options, key).map((item) => item.id));
    chosen[field] = given.filter((item) => `${item.type}_ids` === field && present.has(item.id)).map((item) => item.id);
  }
  const agentOff = options.agent?.disabled_tools ?? [];
  const promptId = session.prompt_snapshot?.prompt_id ?? null;
  const modelId = options.models.some((item) => item.id === session.model_id)
    ? session.model_id
    : defaultSettings(options).modelId;
  return {
    modelId,
    promptKey: promptId && options.prompts.some((item) => item.id === promptId) ? promptId : OWN,
    systemPrompt: session.prompt_snapshot?.system_prompt ?? defaultSettings(options).systemPrompt,
    chosen,
    // a saved agent's run starts again with all of its documents
    ...(options.agent ? { documents: options.resources.documents.map((item) => item.id) } : {}),
    experts: (config.experts ?? []).map((entry) => String(entry.id)).filter((id) => experts.has(id)),
    // the agent's own off tools are locked in the sheet, not part of the chat's choice
    disabledTools: (session.disabled_tools ?? []).filter((name) => !agentOff.includes(name)),
    ...(outputSchema ? { outputSchema } : {}),
  };
}

/** Why a saved agent's run cannot start with these settings: it has resources and none is picked (the API would give
 * it all of them). Null otherwise. */
export function pickReason(options: AgentOptions, settings: AgentSettings): string | null {
  if (!options.agent) return null;
  const has =
    AGENT_KINDS.some(({ key }) => options.resources[key].length > 0) || options.resources.documents.length > 0;
  const picked =
    AGENT_KINDS.some(({ field }) => settings.chosen[field].length > 0) || (settings.documents ?? []).length > 0;
  return has && !picked ? "Pick at least one resource" : null;
}

const SCOPE_FIELD = {
  server: "server_ids",
  service: "service_ids",
  environment: "environment_ids",
  code_project: "code_project_ids",
} as const;
/** The resource a chat is on: a server's, service's, environment's or code project's chat, or writing from one. */
export const scopeResource = (scope: ChatScope): { type: ResourceType; id: string } | undefined =>
  isResourceScope(scope)
    ? { type: scope.kind, id: scope.id }
    : scope.kind === "new-document"
      ? scope.resource
      : undefined;
/** A server, service or environment scope always has its resource chosen (the run is about it); so does the resource
 * writing starts from (Start adds it too), a server only when the run can use it (one without usable SSH is not
 * chosen: the API gives it to the run as its own resource, without SSH). */
export function withScope(scope: ChatScope, settings: AgentSettings, options?: AgentOptions): AgentSettings {
  const resource = scopeResource(scope);
  if (!resource) return settings;
  if (
    !isResourceScope(scope) &&
    resource.type === "server" &&
    !options?.resources.servers.some((item) => item.id === resource.id && item.usable)
  )
    return settings;
  const field = SCOPE_FIELD[resource.type];
  if (settings.chosen[field].includes(resource.id)) return settings;
  return { ...settings, chosen: { ...settings.chosen, [field]: [...settings.chosen[field], resource.id] } };
}

export const PICK_TO_WRITE = "Pick at least one resource in Settings for the agent to write about.";
/** Why writing with an agent cannot start: it is on no resource and none is picked (new_document needs a resource of
 * the run). Null otherwise. */
export function writeReason(scope: ChatScope, settings: AgentSettings): string | null {
  if (scope.kind !== "new-document" || scope.resource) return null;
  return AGENT_KINDS.some(({ field }) => settings.chosen[field].length > 0) ? null : PICK_TO_WRITE;
}

export function startInput(scope: ChatScope, options: AgentOptions, raw: AgentSettings, message: string): StartInput {
  const settings = withScope(scope, raw, options);
  if (scope.kind === "agent") {
    // a saved agent keeps its own model and prompt; the run only narrows it (a document as the agent has it)
    const picked = settings.documents ?? [];
    const schema = settings.outputSchema;
    return {
      ...(schema
        ? { output_schema_id: schema.id, ...(schema.version === null ? {} : { output_schema_version: schema.version }) }
        : {}),
      agent_id: scope.id,
      message: message.trim(),
      context: "",
      expert_ids: settings.experts,
      disabled_tools: settings.disabledTools,
      resources: {
        ...settings.chosen,
        documents: options.resources.documents
          .filter((item) => picked.includes(item.id))
          .map((item) => ({ id: item.id })),
      },
    };
  }
  if (isResourceScope(scope)) {
    // a chat on a server, service, environment or code project: the resource is always among its resources
    return {
      started_from:
        scope.kind === "server"
          ? { server_id: scope.id }
          : scope.kind === "service"
            ? { service_id: scope.id }
            : scope.kind === "environment"
              ? { environment_id: scope.id }
              : { code_project_id: scope.id },
      message: message.trim(),
      model_id: settings.modelId,
      prompt_id: settings.promptKey === OWN ? null : settings.promptKey,
      system_prompt: settings.systemPrompt.trim(),
      context: "",
      resources: { ...settings.chosen, documents: [] },
      expert_ids: settings.experts,
      disabled_tools: settings.disabledTools,
    };
  }
  if (scope.kind === "new-document") {
    // writing: on the resource it starts from, or on the resources picked for it; it names no document
    return {
      started_from: {
        new_document: true,
        ...(scope.resource ? { [`${scope.resource.type}_id`]: scope.resource.id } : {}),
      },
      message: message.trim(),
      model_id: settings.modelId,
      prompt_id: settings.promptKey === OWN ? null : settings.promptKey,
      system_prompt: settings.systemPrompt.trim(),
      context: "",
      resources: { ...settings.chosen, documents: [] },
      expert_ids: settings.experts,
      disabled_tools: settings.disabledTools,
    };
  }
  return {
    started_from: { document_id: scope.id },
    message: message.trim(),
    model_id: settings.modelId,
    prompt_id: settings.promptKey === OWN ? null : settings.promptKey,
    system_prompt: settings.systemPrompt.trim(),
    context: `Document: ${scope.title} (id ${scope.id})`,
    resources: {
      ...settings.chosen,
      documents: options.resources.documents.map((item) => ({ id: item.id })),
    },
    expert_ids: settings.experts,
    disabled_tools: settings.disabledTools,
  };
}

/** "Claude · web-1, Payments": the model and up to three chosen resources (+n more), then the output schema. */
export function settingsSummary(options: AgentOptions, settings: AgentSettings): string {
  const model = options.models.find((item) => item.id === settings.modelId)?.name ?? "No model";
  const names = AGENT_KINDS.flatMap(({ key, field }) =>
    options.resources[key].filter((item) => settings.chosen[field].includes(item.id)).map((item) => item.name),
  );
  const resources =
    names.length === 0
      ? "no resources"
      : names.slice(0, 3).join(", ") + (names.length > 3 ? ` +${names.length - 3}` : "");
  const schema = settings.outputSchema ? ` · Output: ${choiceLabel(settings.outputSchema)}` : "";
  return `${model} · ${resources}${schema}`;
}

/** The Ask agent settings of a document, the chat settings of a saved agent, or the settings for writing with an agent:
 * loaded with the options, remembered in this browser per scope. Null: nothing is loaded (the pane is closed). A changed
 * revision (a saved agent's) reloads the options. */
export function useAgentSettings(scope: ChatScope | null, revision?: number) {
  // The loaded state carries the storage key it belongs to, so a save never writes one scope's settings under another's key.
  // A saved agent's new revision loads its options again (remembered settings of the old revision are dropped).
  const writingOn = scope?.kind === "new-document" ? scope.resource : undefined;
  const key = !scope
    ? null
    : scope.kind === "new-document"
      ? `new-document${writingOn ? `:${writingOn.type}:${writingOn.id}` : ""}`
      : `${scope.kind}:${scope.id}${revision === undefined ? "" : `@${revision}`}`;
  const storage = scope ? STORAGE(scope) : null;
  const [loaded, setLoaded] = useState<{
    key: string;
    storage: string;
    options: AgentOptions;
    settings: AgentSettings;
  } | null>(null);
  const [failed, setFailed] = useState<{ key: string; message: string } | null>(null);
  const kind = scope?.kind ?? null;
  const id = scope && scope.kind !== "new-document" ? scope.id : null;
  useEffect(() => {
    if (!key || !kind || !storage) return;
    const controller = new AbortController();
    const on = writingOn
      ? { [`${writingOn.type === "code_project" ? "codeProject" : writingOn.type}Id`]: writingOn.id }
      : {};
    const which =
      kind === "new-document"
        ? { newDocument: true, ...on }
        : kind === "document"
          ? { documentId: id! }
          : kind === "server"
            ? { serverId: id! }
            : kind === "service"
              ? { serviceId: id! }
              : kind === "environment"
                ? { environmentId: id! }
                : kind === "code_project"
                  ? { codeProjectId: id! }
                  : { agentId: id! };
    // a catalog that cannot be read leaves the saved tool names as they are
    const catalog = getTools()
      .then((value) => value.tools.map((tool) => tool.name))
      .catch(() => undefined);
    Promise.all([getSessionOptions(which, controller.signal), catalog])
      .then(([value, toolNames]) => {
        if (controller.signal.aborted) return;
        let saved: unknown = null;
        try {
          saved = JSON.parse(localStorage.getItem(storage) ?? "null");
        } catch {
          saved = null;
        }
        setFailed(null);
        setLoaded({ key, storage, options: value, settings: restoreSettings(value, saved, toolNames) });
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setFailed({ key, message: e instanceof Error ? e.message : "Could not load the agent options." });
      });
    return () => controller.abort();
  }, [key]);
  const current = key !== null && loaded?.key === key ? loaded : null;
  useEffect(() => {
    if (!current) return;
    try {
      localStorage.setItem(
        current.storage,
        JSON.stringify({
          v: 2,
          ...(current.options.agent ? { revision: current.options.agent.revision } : {}),
          linked: linkedIds(current.options),
          settings: current.settings,
        }),
      );
    } catch {
      /* storage off */
    }
  }, [current]);
  const change = (make: (settings: AgentSettings) => AgentSettings) =>
    setLoaded((state) => (state && state.key === key ? { ...state, settings: make(state.settings) } : state));
  return {
    options: current?.options ?? null,
    settings: current?.settings ?? null,
    error: key !== null && failed?.key === key ? failed.message : "",
    update: (patch: Partial<AgentSettings>) => change((settings) => ({ ...settings, ...patch })),
    replace: (next: AgentSettings) => change(() => next),
  };
}
