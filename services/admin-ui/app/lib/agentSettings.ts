import { useEffect, useState } from 'react';
import { getSessionOptions, toolApplies, type AgentOptions, type AgentResources, type AgentTool, type SessionDetail, type StartInput } from './sessions';

/** The prompt choice for "my own text, no library prompt". */
export const OWN = 'own';
const STORAGE = (documentId: string) => `ask-agent-settings:${documentId}`;
const KINDS: [keyof AgentOptions['resources'], keyof AgentResources][] = [['servers', 'server_ids'], ['services', 'service_ids'],
  ['environments', 'environment_ids'], ['code_projects', 'code_project_ids'], ['collections', 'collection_ids']];
const EMPTY: AgentResources = { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] };

/** What the next Ask agent run on a document starts with. */
export interface AgentSettings {
  modelId: string; promptKey: string; systemPrompt: string; chosen: AgentResources;
  off: string[];  // tools the person unticked (a tool that newly applies starts ticked)
  subagents: boolean; experts: string[];
}

const usable = (item: { id: string; usable?: boolean }) => item.usable !== false;
const linkedIds = (options: AgentOptions) => KINDS.flatMap(([key]) => options.resources[key].filter(item => item.linked).map(item => item.id)).sort();
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];

export function defaultSettings(options: AgentOptions): AgentSettings {
  const first = options.prompts.find(item => item.id === options.default_prompt_id) ?? options.prompts[0];
  const chosen = { ...EMPTY };
  for (const [key, ids] of KINDS) chosen[ids] = options.resources[key].filter(item => item.linked && usable(item)).map(item => item.id);
  return { modelId: options.default_model_id ?? options.models[0]?.id ?? '', promptKey: first ? first.id : OWN,
    systemPrompt: first ? first.content : '', chosen, off: [], subagents: false, experts: [] };
}

/** Saved settings checked against today's options: ids that no longer exist are dropped; resources start again from the
 * document's links when those changed since the save. Anything unreadable gives the defaults. */
export function restoreSettings(options: AgentOptions, saved: unknown): AgentSettings {
  const base = defaultSettings(options);
  if (!saved || typeof saved !== 'object') return base;
  const { v, linked, settings } = saved as { v?: unknown; linked?: unknown; settings?: Record<string, unknown> };
  if (v !== 2 || !settings || typeof settings !== 'object') return base;
  const models = new Set(options.models.map(item => item.id));
  const prompts = new Set(options.prompts.map(item => item.id));
  const tools = new Set(options.tools.map(item => item.name));
  const experts = new Set(options.experts.map(item => item.id));
  const sameLinks = JSON.stringify(strings(linked).sort()) === JSON.stringify(linkedIds(options));
  const chosen = { ...base.chosen };
  if (sameLinks && settings.chosen && typeof settings.chosen === 'object') {
    const savedChosen = settings.chosen as Record<string, unknown>;
    for (const [key, ids] of KINDS) {
      const present = new Set(options.resources[key].filter(usable).map(item => item.id));
      chosen[ids] = strings(savedChosen[ids]).filter(id => present.has(id));
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
  for (const [key, ids] of KINDS) {
    const present = new Set(options.resources[key].filter(usable).map(item => item.id));
    chosen[ids] = strings(resources[ids]).filter(id => present.has(id));
  }
  const promptId = session.prompt_snapshot?.prompt_id ?? null;
  const modelId = options.models.some(item => item.id === session.model_id) ? session.model_id : defaultSettings(options).modelId;
  return { modelId, promptKey: promptId && options.prompts.some(item => item.id === promptId) ? promptId : OWN,
    systemPrompt: session.prompt_snapshot?.system_prompt ?? defaultSettings(options).systemPrompt, chosen,
    off: items.filter(item => choosable.has(item.tool) && item.mode !== 'on').map(item => item.tool),
    subagents: items.some(item => item.tool === 'run_subagent' && item.mode === 'on'),
    experts: (config.experts ?? []).map(entry => String(entry.id)).filter(id => experts.has(id)) };
}

export const shownTools = (options: AgentOptions, settings: AgentSettings): AgentTool[] =>
  options.tools.filter(tool => toolApplies(tool, settings.chosen, options.resources));

export function startInput(documentId: string, documentTitle: string, options: AgentOptions, settings: AgentSettings, message: string): StartInput {
  return { started_from: { document_id: documentId }, message: message.trim(), model_id: settings.modelId,
    prompt_id: settings.promptKey === OWN ? null : settings.promptKey, system_prompt: settings.systemPrompt.trim(),
    context: `Document: ${documentTitle} (id ${documentId})`,
    resources: { ...settings.chosen, documents: options.resources.documents.map(item => ({ id: item.id, editable: item.editable })) },
    tools: shownTools(options, settings).filter(tool => !settings.off.includes(tool.name)).map(tool => tool.name),
    expert_ids: settings.experts, subagents: settings.subagents };
}

/** "Claude · web-1, Payments · 2 tools": the model, up to three chosen resources (+n more) and the tools on. */
export function settingsSummary(options: AgentOptions, settings: AgentSettings): string {
  const model = options.models.find(item => item.id === settings.modelId)?.name ?? 'No model';
  const names = KINDS.flatMap(([key, ids]) => options.resources[key].filter(item => settings.chosen[ids].includes(item.id)).map(item => item.name));
  const resources = names.length === 0 ? 'no resources' : names.slice(0, 3).join(', ') + (names.length > 3 ? ` +${names.length - 3}` : '');
  const on = shownTools(options, settings).filter(tool => !settings.off.includes(tool.name)).length;
  return `${model} · ${resources} · ${on} tool${on === 1 ? '' : 's'}`;
}

/** The document's Ask agent settings: loaded with the options, remembered in this browser per document. Null: nothing
 * is loaded (the pane is closed, or a new document). */
export function useAgentSettings(documentId: string | null) {
  // The loaded state carries the document it belongs to, so a save never writes one document's settings under another's key.
  const [loaded, setLoaded] = useState<{ documentId: string; options: AgentOptions; settings: AgentSettings } | null>(null);
  const [failed, setFailed] = useState<{ documentId: string; message: string } | null>(null);
  useEffect(() => {
    if (!documentId) return;
    const controller = new AbortController();
    getSessionOptions(documentId, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      let saved: unknown = null;
      try { saved = JSON.parse(localStorage.getItem(STORAGE(documentId)) ?? 'null'); } catch { saved = null; }
      setFailed(null); setLoaded({ documentId, options: value, settings: restoreSettings(value, saved) });
    }).catch(e => {
      if (!controller.signal.aborted) setFailed({ documentId, message: e instanceof Error ? e.message : 'Could not load the agent options.' });
    });
    return () => controller.abort();
  }, [documentId]);
  const current = documentId !== null && loaded?.documentId === documentId ? loaded : null;
  useEffect(() => {
    if (!current) return;
    try { localStorage.setItem(STORAGE(current.documentId), JSON.stringify({ v: 2, linked: linkedIds(current.options), settings: current.settings })); }
    catch { /* storage off */ }
  }, [current]);
  const change = (make: (settings: AgentSettings) => AgentSettings) =>
    setLoaded(state => state && state.documentId === documentId ? { ...state, settings: make(state.settings) } : state);
  return { options: current?.options ?? null, settings: current?.settings ?? null,
    error: documentId !== null && failed?.documentId === documentId ? failed.message : '',
    update: (patch: Partial<AgentSettings>) => change(settings => ({ ...settings, ...patch })),
    replace: (next: AgentSettings) => change(() => next) };
}
