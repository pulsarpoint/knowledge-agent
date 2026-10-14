import { useEffect, useState } from 'react';
import { AGENT_KINDS, emptyIds } from '~/lib/resources';
import { getSessionOptions, toolApplies, type AgentOptions, type AgentResources, type AgentTool, type ChatScope, type SessionDetail, type StartInput } from './api';

/** The prompt choice for "my own text, no library prompt". */
export const OWN = 'own';
/** Where a scope's settings are remembered in this browser: per document, per saved agent, one for every new document. */
const STORAGE = (scope: ChatScope) => scope.kind === 'document' ? `ask-agent-settings:${scope.id}`
  : scope.kind === 'agent' ? `agent-settings:agent:${scope.id}`
  : scope.kind === 'server' ? `agent-settings:server:${scope.id}` : 'agent-settings:new-document';
const EMPTY: AgentResources = emptyIds(AGENT_KINDS);

/** What the next Ask agent run on a document starts with. */
export interface AgentSettings {
  modelId: string; promptKey: string; systemPrompt: string; chosen: AgentResources;
  off: string[];  // tools the person unticked (a tool that newly applies starts ticked)
  subagents: boolean; experts: string[];
}

const usable = (item: { id: string; usable?: boolean }) => item.usable !== false;
const linkedIds = (options: AgentOptions) => AGENT_KINDS.flatMap(({ key }) => options.resources[key].filter(item => item.linked).map(item => item.id)).sort();
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];

export function defaultSettings(options: AgentOptions): AgentSettings {
  if (options.agent) {  // a saved agent: its own model and prompt; a per-run agent starts with nothing chosen
    return { modelId: options.agent.model?.id ?? options.default_model_id ?? '', promptKey: OWN, systemPrompt: options.agent.system_prompt,
      chosen: { ...EMPTY }, off: [], subagents: options.agent.subagents_allowed, experts: options.experts.map(item => item.id) };
  }
  const first = options.prompts.find(item => item.id === options.default_prompt_id) ?? options.prompts[0];
  const chosen = { ...EMPTY };
  for (const { key, field } of AGENT_KINDS) chosen[field] = options.resources[key].filter(item => item.linked && usable(item)).map(item => item.id);
  return { modelId: options.default_model_id ?? options.models[0]?.id ?? '', promptKey: first ? first.id : OWN,
    systemPrompt: first ? first.content : '', chosen, off: [], subagents: false, experts: [] };
}

/** Saved settings checked against today's options: ids that no longer exist are dropped; resources start again from the
 * document's links when those changed since the save. Anything unreadable gives the defaults. */
export function restoreSettings(options: AgentOptions, saved: unknown): AgentSettings {
  const base = defaultSettings(options);
  if (!saved || typeof saved !== 'object') return base;
  const { v, revision, linked, settings } = saved as { v?: unknown; revision?: unknown; linked?: unknown; settings?: Record<string, unknown> };
  if (v !== 2 || !settings || typeof settings !== 'object') return base;
  if (options.agent && revision !== options.agent.revision) return base;  // the agent changed since the save
  const models = new Set(options.models.map(item => item.id));
  const prompts = new Set(options.prompts.map(item => item.id));
  const tools = new Set(options.tools.map(item => item.name));
  const experts = new Set(options.experts.map(item => item.id));
  const sameLinks = JSON.stringify(strings(linked).sort()) === JSON.stringify(linkedIds(options));
  const chosen = { ...base.chosen };
  if (sameLinks && settings.chosen && typeof settings.chosen === 'object') {
    const savedChosen = settings.chosen as Record<string, unknown>;
    for (const { key, field } of AGENT_KINDS) {
      const present = new Set(options.resources[key].filter(usable).map(item => item.id));
      chosen[field] = strings(savedChosen[field]).filter(id => present.has(id));
    }
  }
  const modelId = typeof settings.modelId === 'string' && models.has(settings.modelId) ? settings.modelId : base.modelId;
  const promptKey = typeof settings.promptKey === 'string' && (settings.promptKey === OWN || prompts.has(settings.promptKey))
    ? settings.promptKey : base.promptKey;
  const systemPrompt = typeof settings.systemPrompt === 'string' && settings.systemPrompt.trim() ? settings.systemPrompt : base.systemPrompt;
  return { modelId, promptKey, systemPrompt, chosen, off: strings(settings.off).filter(name => tools.has(name)),
    subagents: settings.subagents === true, experts: strings(settings.experts).filter(id => experts.has(id)) };
}

/** The settings a run started with, from its saved configuration (for Start new chat with these settings), checked
 * against today's options like restored settings: resources that are gone or not usable, experts that are gone and a
 * model that is gone are left out (the default model instead). */
export function settingsFromSession(options: AgentOptions, session: SessionDetail): AgentSettings {
  const config = (session.config ?? {}) as { tools?: { items?: { tool: string; mode: string }[] }; experts?: { id: string }[] };
  const items = config.tools?.items ?? [];
  const choosable = new Set(options.tools.map(tool => tool.name));
  const experts = new Set(options.experts.map(item => item.id));
  const resources = (session.run_resources ?? {}) as Partial<Record<keyof AgentResources, unknown>>;
  const chosen = { ...EMPTY };
  for (const { key, field } of AGENT_KINDS) {
    const present = new Set(options.resources[key].filter(usable).map(item => item.id));
    chosen[field] = strings(resources[field]).filter(id => present.has(id));
  }
  const promptId = session.prompt_snapshot?.prompt_id ?? null;
  const modelId = options.models.some(item => item.id === session.model_id) ? session.model_id : defaultSettings(options).modelId;
  return { modelId, promptKey: promptId && options.prompts.some(item => item.id === promptId) ? promptId : OWN,
    systemPrompt: session.prompt_snapshot?.system_prompt ?? defaultSettings(options).systemPrompt, chosen,
    off: items.filter(item => choosable.has(item.tool) && item.mode !== 'on').map(item => item.tool),
    subagents: (!options.agent || options.agent.subagents_allowed) && items.some(item => item.tool === 'run_subagent' && item.mode === 'on'),
    experts: (config.experts ?? []).map(entry => String(entry.id)).filter(id => experts.has(id)) };
}

/** A saved agent with fixed resources lists none in its options, so no tool is filtered by what is chosen. */
export const shownTools = (options: AgentOptions, settings: AgentSettings): AgentTool[] =>
  options.agent?.resources_fixed ? options.tools : options.tools.filter(tool => toolApplies(tool, settings.chosen, options.resources));

/** A server scope always has its server chosen (the run is about it), so the tools that need a server show and are sent. */
export function withScope(scope: ChatScope, settings: AgentSettings): AgentSettings {
  if (scope.kind !== 'server' || settings.chosen.server_ids.includes(scope.id)) return settings;
  return { ...settings, chosen: { ...settings.chosen, server_ids: [...settings.chosen.server_ids, scope.id] } };
}

export function startInput(scope: ChatScope, options: AgentOptions, raw: AgentSettings, message: string): StartInput {
  const settings = withScope(scope, raw);
  const tools = shownTools(options, settings).filter(tool => !settings.off.includes(tool.name)).map(tool => tool.name);
  if (scope.kind === 'agent') {  // a saved agent keeps its own model and prompt; the run only narrows it
    return { agent_id: scope.id, message: message.trim(), context: '', tools, expert_ids: settings.experts, subagents: settings.subagents,
      resources: { ...settings.chosen, environment_ids: [], documents: [] } };
  }
  if (scope.kind === 'server') {  // a discovery run from the server: the server is always among its resources
    return { started_from: { server_id: scope.id }, message: message.trim(), model_id: settings.modelId,
      prompt_id: settings.promptKey === OWN ? null : settings.promptKey, system_prompt: settings.systemPrompt.trim(), context: '',
      resources: { ...settings.chosen, documents: [] }, tools, expert_ids: settings.experts, subagents: settings.subagents };
  }
  if (scope.kind === 'new-document') {  // the Title and Folder become the one document the run may write
    const document = { folder: scope.folder.trim(), name: scope.title.trim() };
    return { started_from: { new_document: document }, message: message.trim(), model_id: settings.modelId,
      prompt_id: settings.promptKey === OWN ? null : settings.promptKey, system_prompt: settings.systemPrompt.trim(), context: '',
      resources: { ...settings.chosen, documents: [{ new: document, editable: true }] },
      tools, expert_ids: settings.experts, subagents: settings.subagents };
  }
  return { started_from: { document_id: scope.id }, message: message.trim(), model_id: settings.modelId,
    prompt_id: settings.promptKey === OWN ? null : settings.promptKey, system_prompt: settings.systemPrompt.trim(),
    context: `Document: ${scope.title} (id ${scope.id})`,
    resources: { ...settings.chosen, documents: options.resources.documents.map(item => ({ id: item.id, editable: item.editable })) },
    tools, expert_ids: settings.experts, subagents: settings.subagents };
}

/** "Claude · web-1, Payments · 2 tools": the model, up to three chosen resources (+n more) and the tools on. */
export function settingsSummary(options: AgentOptions, settings: AgentSettings): string {
  const model = options.models.find(item => item.id === settings.modelId)?.name ?? 'No model';
  const names = AGENT_KINDS.flatMap(({ key, field }) => options.resources[key].filter(item => settings.chosen[field].includes(item.id)).map(item => item.name));
  const resources = names.length === 0 ? 'no resources' : names.slice(0, 3).join(', ') + (names.length > 3 ? ` +${names.length - 3}` : '');
  const on = shownTools(options, settings).filter(tool => !settings.off.includes(tool.name)).length;
  return `${model} · ${resources} · ${on} tool${on === 1 ? '' : 's'}`;
}

/** The Ask agent settings of a document, the Test settings of a saved agent, or the settings for writing a new document:
 * loaded with the options, remembered in this browser per scope. Null: nothing is loaded (the pane is closed). A changed
 * revision (a saved agent's) reloads the options. A new document's options load once per page (not per typed title). */
export function useAgentSettings(scope: ChatScope | null, revision?: number) {
  // The loaded state carries the storage key it belongs to, so a save never writes one scope's settings under another's key.
  // A saved agent's new revision loads its options again (remembered settings of the old revision are dropped).
  const key = !scope ? null : scope.kind === 'new-document' ? 'new-document'
    : `${scope.kind}:${scope.id}${revision === undefined ? '' : `@${revision}`}`;
  const storage = scope ? STORAGE(scope) : null;
  const [loaded, setLoaded] = useState<{ key: string; storage: string; options: AgentOptions; settings: AgentSettings } | null>(null);
  const [failed, setFailed] = useState<{ key: string; message: string } | null>(null);
  const kind = scope?.kind ?? null;
  const id = scope && scope.kind !== 'new-document' ? scope.id : null;
  useEffect(() => {
    if (!key || !kind || !storage) return;
    const controller = new AbortController();
    const which = kind === 'new-document' ? { newDocument: true } : kind === 'document' ? { documentId: id! }
      : kind === 'server' ? { serverId: id! } : { agentId: id! };
    getSessionOptions(which, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      let saved: unknown = null;
      try { saved = JSON.parse(localStorage.getItem(storage) ?? 'null'); } catch { saved = null; }
      setFailed(null); setLoaded({ key, storage, options: value, settings: restoreSettings(value, saved) });
    }).catch(e => {
      if (!controller.signal.aborted) setFailed({ key, message: e instanceof Error ? e.message : 'Could not load the agent options.' });
    });
    return () => controller.abort();
  }, [key]);
  const current = key !== null && loaded?.key === key ? loaded : null;
  useEffect(() => {
    if (!current) return;
    try {
      localStorage.setItem(current.storage, JSON.stringify({ v: 2, ...(current.options.agent ? { revision: current.options.agent.revision } : {}),
        linked: linkedIds(current.options), settings: current.settings }));
    } catch { /* storage off */ }
  }, [current]);
  const change = (make: (settings: AgentSettings) => AgentSettings) =>
    setLoaded(state => state && state.key === key ? { ...state, settings: make(state.settings) } : state);
  return { options: current?.options ?? null, settings: current?.settings ?? null,
    error: key !== null && failed?.key === key ? failed.message : '',
    update: (patch: Partial<AgentSettings>) => change(settings => ({ ...settings, ...patch })),
    replace: (next: AgentSettings) => change(() => next) };
}
