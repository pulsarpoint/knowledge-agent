import { useEffect, useState } from 'react';
import { getAgentOptions, toolApplies, type AgentOptions, type AgentResources, type AgentTool, type StartInput } from './agentSessions';
import type { SessionDetail } from './generations';

export const BUILT_IN = 'built-in';
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
  const first = options.prompts[0];
  const chosen = { ...EMPTY };
  for (const [key, ids] of KINDS) chosen[ids] = options.resources[key].filter(item => item.linked && usable(item)).map(item => item.id);
  return { modelId: options.default_model_id ?? options.models[0]?.id ?? '', promptKey: first ? first.id : BUILT_IN,
    systemPrompt: first ? first.content : options.default_prompt.content, chosen, off: [], subagents: false, experts: [] };
}

/** Saved settings checked against today's options: ids that no longer exist are dropped; resources start again from the
 * document's links when those changed since the save. Anything unreadable gives the defaults. */
export function restoreSettings(options: AgentOptions, saved: unknown): AgentSettings {
  const base = defaultSettings(options);
  if (!saved || typeof saved !== 'object') return base;
  const { v, linked, settings } = saved as { v?: unknown; linked?: unknown; settings?: Record<string, unknown> };
  if (v !== 1 || !settings || typeof settings !== 'object') return base;
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
  const promptKey = typeof settings.promptKey === 'string' && (settings.promptKey === BUILT_IN || prompts.has(settings.promptKey))
    ? settings.promptKey : base.promptKey;
  const systemPrompt = typeof settings.systemPrompt === 'string' && settings.systemPrompt.trim() ? settings.systemPrompt : base.systemPrompt;
  return { modelId, promptKey, systemPrompt, chosen, off: strings(settings.off).filter(name => tools.has(name)),
    subagents: settings.subagents === true, experts: strings(settings.experts).filter(id => experts.has(id)) };
}

/** The settings a run started with, from its saved configuration (for Start new chat with these settings). */
export function settingsFromSession(options: AgentOptions, session: SessionDetail): AgentSettings {
  const config = (session.config ?? {}) as { tools?: { items?: { tool: string; mode: string }[] }; experts?: { id: string }[] };
  const items = config.tools?.items ?? [];
  const choosable = new Set(options.tools.map(tool => tool.name));
  const resources = (session.run_resources ?? {}) as Partial<Record<keyof AgentResources, unknown>>;
  const chosen = { ...EMPTY };
  for (const [, ids] of KINDS) chosen[ids] = strings(resources[ids]);
  const promptId = session.prompt_snapshot?.prompt_id ?? null;
  return { modelId: session.model_id, promptKey: promptId && options.prompts.some(item => item.id === promptId) ? promptId : BUILT_IN,
    systemPrompt: session.prompt_snapshot?.system_prompt ?? options.default_prompt.content, chosen,
    off: items.filter(item => choosable.has(item.tool) && item.mode !== 'on').map(item => item.tool),
    subagents: items.some(item => item.tool === 'run_subagent' && item.mode === 'on'),
    experts: (config.experts ?? []).map(entry => String(entry.id)) };
}

export const shownTools = (options: AgentOptions, settings: AgentSettings): AgentTool[] =>
  options.tools.filter(tool => toolApplies(tool, settings.chosen, options.resources));

export function startInput(documentId: string, options: AgentOptions, settings: AgentSettings, instruction: string): StartInput {
  return { kind: 'revision', target: { document_id: documentId }, instruction: instruction.trim(), model_id: settings.modelId,
    prompt_id: settings.promptKey === BUILT_IN ? null : settings.promptKey, system_prompt: settings.systemPrompt.trim(),
    resources: settings.chosen, tools: shownTools(options, settings).filter(tool => !settings.off.includes(tool.name)).map(tool => tool.name),
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
  const [options, setOptions] = useState<AgentOptions | null>(null);
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setOptions(null); setSettings(null); setError('');
    if (!documentId) return () => controller.abort();
    getAgentOptions(documentId, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      let saved: unknown = null;
      try { saved = JSON.parse(localStorage.getItem(STORAGE(documentId)) ?? 'null'); } catch { saved = null; }
      setOptions(value); setSettings(restoreSettings(value, saved));
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load the agent options.'); });
    return () => controller.abort();
  }, [documentId]);
  useEffect(() => {
    if (!documentId || !options || !settings) return;
    try { localStorage.setItem(STORAGE(documentId), JSON.stringify({ v: 1, linked: linkedIds(options), settings })); } catch { /* storage off */ }
  }, [documentId, options, settings]);
  return { options, error, settings,
    update: (patch: Partial<AgentSettings>) => setSettings(current => current && { ...current, ...patch }),
    replace: (next: AgentSettings) => setSettings(next) };
}
