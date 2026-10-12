# Ask agent chat pane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The document page splits into the document and an Ask agent chat pane; the agent's settings live in a
Settings sheet opened from the pane; an agent draft shows as a bar above the editor.

**Architecture:** UI only (admin-ui). A settings module (`lib/agentSettings.ts`) owns the run settings (defaults from
the options, remembered per document in `localStorage`, rebuilt from a run's saved configuration) and builds the
`StartInput`. `AgentSettingsSheet` edits or shows them. `AgentPane` holds one conversation (the existing `RunWorkspace`
in a new compact mode): the first message starts a run through `POST /api/agent-sessions`, later ones go to the run.
`DocumentEditorView` lays out the editor and the pane side by side, shows the draft bar, and the run deep link
`/documents/:documentId/agent/:runId` opens the same page with the pane on that run.

**Tech Stack:** React Router 7, shadcn/ui (Radix), Tailwind, vitest + Testing Library; pnpm.

**Spec:** `docs/superpowers/specs/2026-10-12-document-agent-pane-design.md` (builds on
`docs/superpowers/specs/2026-10-12-document-agent-design.md`).

## Global Constraints

- Work only in the worktree `.claude/worktrees/document-agent-pane` on branch `feature/document-agent-pane`.
- Never run `make` targets. Never touch `.env` or anything under `.local`. Never `git stash`. Never touch the running
  Docker stack or its database.
- Never `git add .superpowers`; never commit `node_modules` or `services/admin-ui/build`.
- No backend changes; no new npm dependencies; no new shadcn components (use those in `app/components/ui`).
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- UI checks: `cd services/admin-ui && npx vitest run <files>`, `npx tsc --noEmit`, `npx react-router routes >/dev/null`.
  If `node_modules` is missing, run `pnpm install --frozen-lockfile` there (pnpm, not npm).
- `localStorage` reads and writes are wrapped in try/catch; the page works without it.
- UI texts, exactly:
  - Document page toggle button `Ask agent` (`aria-pressed` reflects the pane).
  - Pane: region label `Ask agent`; buttons `Settings`, `New chat`, `Earlier runs`; empty text
    `Ask the agent to change this document.`; settings summary labelled `Current settings`; message box label
    `Message`, placeholder `For example: add a section on log rotation`; closed run
    `This conversation is closed. Start a New chat.`; note under a finished turn without a save
    `No draft saved in this turn`.
  - Settings dot: the Settings button's accessible name becomes `Settings (check the settings)` after a refused start.
  - New chat while working: dialog `Close this conversation?`, text `The agent is still working. Closing stops it.`,
    buttons `Keep it` / `Close and start new`.
  - Sheet: title `Agent settings`; editable description
    `What the agent may use in the next conversation on “{title}”.`; read-only description
    `This conversation's settings. They cannot change while it is open.`; buttons `Preview`, `Done`,
    `Start new chat with these settings`; preview `pre` label `Configuration preview`; saved configuration `pre`
    label `Run configuration`.
  - Draft bar: region label `Agent draft`; text `Agent draft · based on version {n}`; badge `stale`; buttons `Compare`,
    `Promote`; on a stale refusal the bar shows the server's message with `Promote anyway` and `Cancel`.
  - Divider: `role="separator"`, `aria-orientation="vertical"`, `aria-label="Resize the agent pane"`.
- Storage keys, exactly: `ask-agent-pane` (`'open'` | `'closed'`), `ask-agent-pane-width` (pane percent, 25–60,
  default 40), `ask-agent-settings:{documentId}` (`{v: 1, linked: string[], settings: AgentSettings}`).

## Review Focus

1. A Promote from the draft bar or the Drafts tab remounts the editor (`refresh`): the pane must survive it — same run,
   same conversation, no reload of the run list into a different run (Task 3 test
   `the pane keeps its conversation when a draft is promoted`).
2. Settings remembered for a document whose links changed, or that name a model, prompt, tool or expert that no longer
   exists: stale ids must be dropped, never sent (Task 1 test `restored settings drop what the options no longer have`).
3. A first message typed while the editor has unsaved changes: ask first; Keep editing must keep the typed message and
   start nothing (Task 3 test `the first message asks about unsaved changes first`).
4. A refused start: the message stays in the box and the reason shows; the next Send retries with the current settings
   (Task 2 test `a refused start keeps the message and marks Settings`).
5. Opening a run deep link for a run of another document, or a run id that does not exist: the pane shows the error and
   New chat still works (Task 2 test `an unknown run shows the error and New chat clears it`).

---

### Task 1: Settings module and the Settings sheet

**Files:**
- Create: `services/admin-ui/app/lib/agentSettings.ts`
- Create: `services/admin-ui/app/components/AgentSettingsSheet.tsx`
- Create: `services/admin-ui/app/lib/__tests__/agentSettings.test.ts` (create the `__tests__` folder if missing; if the
  project keeps lib tests elsewhere, e.g. `app/components/__tests__`, put it there and say so)
- Create: `services/admin-ui/app/components/__tests__/agentSettingsSheet.test.tsx`

**Interfaces:**
- Consumes: `lib/agentSessions.ts` (`AgentOptions`, `AgentResources`, `AgentTool`, `StartInput`, `toolApplies`,
  `getAgentOptions`, `previewAgentSession`); `lib/generations.ts` (`SessionDetail`).
- Produces (`lib/agentSettings.ts`):
  - `export const BUILT_IN = 'built-in'`
  - `export interface AgentSettings { modelId: string; promptKey: string; systemPrompt: string; chosen: AgentResources; off: string[]; subagents: boolean; experts: string[] }`
  - `export function defaultSettings(options: AgentOptions): AgentSettings`
  - `export function restoreSettings(options: AgentOptions, saved: unknown): AgentSettings`
  - `export function settingsFromSession(options: AgentOptions, session: SessionDetail): AgentSettings`
  - `export function shownTools(options: AgentOptions, settings: AgentSettings): AgentTool[]`
  - `export function startInput(documentId: string, options: AgentOptions, settings: AgentSettings, instruction: string): StartInput`
  - `export function settingsSummary(options: AgentOptions, settings: AgentSettings): string`
  - `export function useAgentSettings(documentId: string | null): { options: AgentOptions | null; error: string; settings: AgentSettings | null; update: (patch: Partial<AgentSettings>) => void; replace: (next: AgentSettings) => void }`
- Produces (`components/AgentSettingsSheet.tsx`): default export
  `AgentSettingsSheet({ open, onOpenChange, documentId, documentTitle, options, settings, onChange, previewMessage, session, onStartNew })`
  - `options: AgentOptions`, `settings: AgentSettings`, `onChange: (patch: Partial<AgentSettings>) => void`,
    `previewMessage: string` (the message box text), `session: SessionDetail | null` (an open run → read-only),
    `onStartNew: () => void` (Start new chat with these settings).

- [ ] **Step 1: Write the failing module test**

`services/admin-ui/app/lib/__tests__/agentSettings.test.ts`:

```ts
import { BUILT_IN, defaultSettings, restoreSettings, settingsFromSession, settingsSummary, shownTools, startInput } from '../agentSettings';
import type { AgentOptions } from '../agentSessions';
import type { SessionDetail } from '../generations';

export const options: AgentOptions = {
  models: [{ id: 'm1', name: 'GPT' }, { id: 'm2', name: 'Claude' }], default_model_id: 'm2',
  prompts: [{ id: 'p1', name: 'Careful reviser', content: 'You revise carefully.' }],
  default_prompt: { id: null, name: 'Built-in', content: 'Built-in prompt.' },
  resources: {
    servers: [{ id: 's1', name: 'web-1', linked: true, usable: true, reason: null },
      { id: 's2', name: 'prod-1', linked: true, usable: false, reason: 'SSH is not set up for this server.' }],
    services: [{ id: 'v1', name: 'Payments', linked: true, brings_code: false, brings_servers: true }],
    environments: [], code_projects: [{ id: 'c1', name: 'Shop', linked: false }], collections: [] },
  tools: [{ name: 'ssh_run', label: 'SSH', summary: 'Run a command', group: 'servers', needs: 'ssh', requires: 'at least one server' },
    { name: 'read_file', label: 'Read file', summary: 'Read a file', group: 'code', needs: 'files', requires: 'a code project or a collection' },
    { name: 'get_service_info', label: 'Service info', summary: 'A service', group: 'inventory', needs: null, requires: null }],
  fixed_tools: [{ name: 'get_doc', label: 'Read the document' }, { name: 'save_doc', label: 'Save the document' }, { name: 'edit_doc', label: 'Edit the document' }],
  experts: [{ id: 'e1', name: 'Payments expert', when_to_use: 'Payments questions' }],
};

test('defaults: the default model, the first tagged prompt, the usable linked resources, every tool on', () => {
  const settings = defaultSettings(options);
  expect(settings).toEqual({ modelId: 'm2', promptKey: 'p1', systemPrompt: 'You revise carefully.',
    chosen: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: [], collection_ids: [] },
    off: [], subagents: false, experts: [] });
  expect(shownTools(options, settings).map(tool => tool.name)).toEqual(['ssh_run', 'get_service_info']);
  expect(defaultSettings({ ...options, prompts: [] })).toMatchObject({ promptKey: BUILT_IN, systemPrompt: 'Built-in prompt.' });
});

test('startInput sends only the shown tools that are on, and the message as the instruction', () => {
  const settings = { ...defaultSettings(options), off: ['get_service_info'] };
  expect(startInput('doc-1', options, settings, '  Add log rotation.  ')).toEqual({
    kind: 'revision', target: { document_id: 'doc-1' }, instruction: 'Add log rotation.', model_id: 'm2', prompt_id: 'p1',
    system_prompt: 'You revise carefully.', resources: settings.chosen, tools: ['ssh_run'], expert_ids: [], subagents: false });
  expect(startInput('doc-1', options, { ...settings, promptKey: BUILT_IN }, 'x').prompt_id).toBeNull();
});

test('restored settings drop what the options no longer have', () => {
  const linked = ['s1', 's2', 'v1'];
  const saved = { v: 1, linked, settings: { modelId: 'gone', promptKey: 'gone', systemPrompt: 'Mine.', subagents: true,
    chosen: { server_ids: ['s1', 's9'], service_ids: [], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] },
    off: ['ssh_run', 'gone_tool'], experts: ['e1', 'e9'] } };
  expect(restoreSettings(options, saved)).toEqual({ modelId: 'm2', promptKey: 'p1', systemPrompt: 'Mine.', subagents: true,
    chosen: { server_ids: ['s1'], service_ids: [], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] },
    off: ['ssh_run'], experts: ['e1'] });
  // the document's links changed since: the resources start again from the links
  expect(restoreSettings(options, { ...saved, linked: ['s1'] }).chosen).toEqual(defaultSettings(options).chosen);
  expect(restoreSettings(options, 'garbage')).toEqual(defaultSettings(options));
  expect(restoreSettings(options, { v: 2, linked, settings: saved.settings })).toEqual(defaultSettings(options));
});

test('settingsFromSession rebuilds the settings a run started with', () => {
  const session = { model_id: 'm1', prompt_snapshot: { system_prompt: 'Run prompt.', prompt_id: null },
    run_resources: { server_ids: ['s1'], service_ids: [], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] },
    config: { tools: { items: [{ tool: 'ssh_run', mode: 'on' }, { tool: 'read_file', mode: 'off' }, { tool: 'get_service_info', mode: 'on' },
      { tool: 'run_subagent', mode: 'on' }, { tool: 'get_doc', mode: 'on' }] }, experts: [{ id: 'e1', name: 'Payments expert' }] },
  } as unknown as SessionDetail;
  expect(settingsFromSession(options, session)).toEqual({ modelId: 'm1', promptKey: BUILT_IN, systemPrompt: 'Run prompt.',
    chosen: { server_ids: ['s1'], service_ids: [], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] },
    off: ['read_file'], subagents: true, experts: ['e1'] });
});

test('the summary names the model, up to three resources and the tools on', () => {
  expect(settingsSummary(options, defaultSettings(options))).toBe('Claude · web-1, Payments · 2 tools');
  const many = { ...defaultSettings(options), chosen: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] } };
  expect(settingsSummary(options, { ...many, chosen: { ...many.chosen, server_ids: ['s1', 's2'] } })).toBe('Claude · web-1, prod-1, Payments +1 · 3 tools');
  expect(settingsSummary(options, { ...defaultSettings(options), chosen: { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] } }))
    .toBe('Claude · no resources · 1 tool');
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-ui && npx vitest run app/lib/__tests__/agentSettings.test.ts`
Expected: FAIL (cannot resolve `../agentSettings`).

- [ ] **Step 3: Write `lib/agentSettings.ts`**

```ts
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

const usable = (item: { usable?: boolean }) => item.usable !== false;
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
```

Notes: `AgentServer` has `usable`; other resource items do not (`usable(item)` treats a missing flag as usable). If
`RunResourcesInput`'s keys differ from `AgentResources`', map them in `settingsFromSession` and say so in the report.

- [ ] **Step 4: Run the module test to verify it passes**

Run: `cd services/admin-ui && npx vitest run app/lib/__tests__/agentSettings.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the failing sheet test**

`services/admin-ui/app/components/__tests__/agentSettingsSheet.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import AgentSettingsSheet from '../AgentSettingsSheet';
import { defaultSettings, type AgentSettings } from '~/lib/agentSettings';
import type { SessionDetail } from '~/lib/generations';
import { options } from '~/lib/__tests__/agentSettings.test';

afterEach(() => vi.unstubAllGlobals());

function Harness({ session = null, onStartNew = () => {}, message = '' }: { session?: SessionDetail | null; onStartNew?: () => void; message?: string }) {
  const [settings, setSettings] = useState<AgentSettings>(defaultSettings(options));
  return <>
    <AgentSettingsSheet open onOpenChange={() => {}} documentId="doc-1" documentTitle="Runbook" options={options} settings={settings}
      onChange={patch => setSettings(current => ({ ...current, ...patch }))} previewMessage={message} session={session} onStartNew={onStartNew} />
    <output aria-label="Settings state">{JSON.stringify(settings)}</output>
  </>;
}

test('editable: linked resources ticked, unusable server disabled, a newly applying tool starts ticked', () => {
  render(<Harness />);
  expect(screen.getByRole('heading', { name: 'Agent settings' })).toBeInTheDocument();
  expect(screen.getByText('What the agent may use in the next conversation on “Runbook”.')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'web-1' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'prod-1' })).toBeDisabled();
  expect(screen.getByText('SSH is not set up for this server.')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'Read the document' })).toBeDisabled();
  expect(screen.queryByRole('checkbox', { name: 'Read file' })).toBeNull();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Shop' }));
  expect(screen.getByRole('checkbox', { name: 'Read file' })).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'SSH' }));
  expect(JSON.parse(screen.getByLabelText('Settings state').textContent!).off).toEqual(['ssh_run']);
});

test('Preview sends the message box text, or a placeholder when it is empty', async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    bodies.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ config: { model: { name: 'Claude' } }, first_message: 'x' }), { status: 200 });
  }));
  const { rerender } = render(<Harness />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  expect((await screen.findByLabelText('Configuration preview')).textContent).toContain('"name": "Claude"');
  expect((bodies[0] as { instruction: string }).instruction).toBe('(your next message)');
  rerender(<Harness message="Add log rotation." />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await waitFor(() => expect((bodies[1] as { instruction: string }).instruction).toBe('Add log rotation.'));
});

test('read-only for an open run: its saved configuration, and Start new chat with these settings', () => {
  const onStartNew = vi.fn();
  const session = { id: 'r1', status: 'waiting', config: { model: { name: 'GPT' } } } as unknown as SessionDetail;
  render(<Harness session={session} onStartNew={onStartNew} />);
  expect(screen.getByText("This conversation's settings. They cannot change while it is open.")).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'web-1' })).toBeDisabled();
  expect(screen.getByLabelText('Run configuration').textContent).toContain('"name": "GPT"');
  expect(screen.queryByRole('button', { name: 'Preview' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Start new chat with these settings' }));
  expect(onStartNew).toHaveBeenCalledOnce();
});
```

(The sheet test imports the shared `options` fixture from the module test; if importing a test module is a problem in
this vitest setup, move `options` to `app/lib/__tests__/agentOptionsFixture.ts` and import it from both.)

- [ ] **Step 6: Run it to verify it fails**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/agentSettingsSheet.test.tsx`
Expected: FAIL (cannot resolve `../AgentSettingsSheet`).

- [ ] **Step 7: Write `AgentSettingsSheet.tsx`**

Take the form markup of `components/AskAgentSheet.tsx` (Model select, Servers and resources, Tools with the fixed
tools first, Agents with the subagents switch and experts, the Advanced collapsible with Prompt and System prompt) and
make it controlled by `settings` / `onChange` instead of local state. Differences from `AskAgentSheet`:

```tsx
/** The Ask agent settings for a document: editable before a conversation starts (Preview shows the JSON the next message
 * starts with), read-only while a conversation is open (its saved configuration; Start new chat with these settings). */
export default function AgentSettingsSheet({ open, onOpenChange, documentId, documentTitle, options, settings, onChange,
  previewMessage, session, onStartNew }: {
  open: boolean; onOpenChange: (open: boolean) => void; documentId: string; documentTitle: string; options: AgentOptions;
  settings: AgentSettings; onChange: (patch: Partial<AgentSettings>) => void; previewMessage: string;
  session: SessionDetail | null; onStartNew: () => void }) {
  const locked = session !== null && session.status !== 'closed';
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => { setPreview(null); }, [settings, previewMessage]);  // a changed input makes the preview stale
  const shown = shownTools(options, settings);
  // … markup:
  //  <Sheet open={open} onOpenChange={next => { if (!busy) onOpenChange(next); }}>
  //   <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
  //    <SheetHeader><SheetTitle>Agent settings</SheetTitle><SheetDescription>{locked
  //      ? "This conversation's settings. They cannot change while it is open."
  //      : `What the agent may use in the next conversation on “${documentTitle}”.`}</SheetDescription></SheetHeader>
  //    error Alert;
  //    <fieldset disabled={locked} className="flex min-w-0 flex-col gap-6"> … the form (every control also gets
  //      disabled={locked} where Radix ignores the fieldset: Checkbox, Switch, Select) … </fieldset>
  //    locked → <pre aria-label="Run configuration" className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">
  //               {JSON.stringify(session!.config ?? {}, null, 2)}</pre>
  //             and <Button onClick={onStartNew}>Start new chat with these settings</Button>
  //    !locked → preview pre (aria-label "Configuration preview") when preview !== null;
  //              buttons: Preview (variant outline; disabled while busy or without a model or system prompt; runs
  //              previewAgentSession(startInput(documentId, options, settings, previewMessage.trim() || '(your next message)'))
  //              and sets JSON.stringify(result, null, 2); a refusal shows in the error Alert) and Done (onOpenChange(false)).
  //   </SheetContent></Sheet>
}
```

Write the JSX in full from `AskAgentSheet.tsx`'s markup (same ids prefixed `settings-` instead of `ask-`, same
aria-labels). Toggling a resource: `onChange({ chosen: { ...settings.chosen, [key]: on ? [...ids, id] : ids.filter(…) } })`;
a tool: `onChange({ off: on ? off.filter(n => n !== name) : [...off, name] })`; a prompt choice sets `promptKey` and
`systemPrompt` (the prompt's content, or `options.default_prompt.content` for `BUILT_IN`).

- [ ] **Step 8: Run the tests**

Run: `cd services/admin-ui && npx vitest run app/lib/__tests__/agentSettings.test.ts app/components/__tests__/agentSettingsSheet.test.tsx && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 9: Commit**

```bash
git add services/admin-ui/app/lib/agentSettings.ts services/admin-ui/app/components/AgentSettingsSheet.tsx \
  services/admin-ui/app/lib/__tests__/agentSettings.test.ts services/admin-ui/app/components/__tests__/agentSettingsSheet.test.tsx
git commit -m "feat(admin-ui): Ask agent settings — remembered per document, rebuilt from a run, editable or read-only sheet

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The chat pane

**Files:**
- Modify: `services/admin-ui/app/components/RunWorkspace.tsx` (a `compact` prop: no side column; the "Run details"
  drawer button shows at every width)
- Modify: `services/admin-ui/app/lib/agentSessions.ts` (move `savedIn` here from `DocumentAgentRunView.tsx`; keep a
  re-export in `DocumentAgentRunView.tsx` until Task 3 deletes that file)
- Create: `services/admin-ui/app/components/AgentPane.tsx`
- Create: `services/admin-ui/app/components/__tests__/agentPane.test.tsx`

**Interfaces:**
- Consumes: Task 1 (`useAgentSettings` result shape, `startInput`, `settingsSummary`, `settingsFromSession`,
  `AgentSettingsSheet`); `lib/agentSessions.ts` (`getDocumentAgentRuns`, `startAgentSession`, `AgentRunSummary`);
  `lib/generations.ts` (`sendMessage`, `stopSession`, `closeSession`, `SessionDetail`); `useSessionEvents`,
  `useSessionNotice`; `RunWorkspace`; `runPath` from `lib/runs`.
- Produces: default export `AgentPane({ documentId, documentTitle, settings, initialRunId, confirmUnsaved, onDraftChange, onRunChange })`
  - `settings: ReturnType<typeof useAgentSettings>` (the hook lives in the page so the sheet and the summary share it)
  - `initialRunId: string | null` (the deep link's run)
  - `confirmUnsaved: (run: () => void) => void` (the editor's unsaved-changes confirmation)
  - `onDraftChange: (draft: SessionDetail['document_draft'] | null) => void` (the pane's run's draft, for the bar)
  - `onRunChange?: (runId: string | null) => void`
- `RunWorkspaceProps` gains `compact?: boolean`.
- `lib/agentSessions.ts` exports `savedIn(turn: Turn): boolean`.

- [ ] **Step 1: Write the failing pane test**

`services/admin-ui/app/components/__tests__/agentPane.test.tsx`:

```tsx
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import AgentPane from '../AgentPane';
import { defaultSettings } from '~/lib/agentSettings';
import { options } from '~/lib/__tests__/agentSettings.test';

const live = vi.hoisted(() => ({ sessions: {} as Record<string, any>, requested: [] as (string | null)[] }));
vi.mock('~/lib/useSessionEvents', () => ({
  useSessionEvents: (id: string | null) => {
    live.requested.push(id);
    const session = id ? live.sessions[id] ?? null : null;
    return { session, setSession: vi.fn(), error: id && !session ? 'Session not found' : '', setError: vi.fn(), events: [],
      older: 'start', loadOlder: vi.fn(), toolCounts: undefined, reconnecting: false };
  },
  useSessionNotice: () => ({ notice: null, clear: vi.fn(), stopping: vi.fn(), sent: vi.fn() }),
}));
vi.mock('../RunWorkspace', () => ({ default: ({ run, heading, headerActions, empty, composer, compact }: Record<string, any>) => <div data-compact={String(compact)}>
  {heading}{headerActions}{run.error && <p>{run.error}</p>}{empty}
  <label>Message<textarea value={composer.value} onChange={e => composer.onChange(e.target.value)} placeholder={composer.placeholder} /></label>
  {composer.blockedReason && <p>{composer.blockedReason}</p>}
  <button type="button" onClick={composer.onSend}>Send message</button></div> }));

const at = '2026-10-12T10:00:00Z';
const run = (id: string, status: string, title = `Run ${id}`) => ({ id, title, status, model_name: 'GPT', started_by: null, created_at: at, last_activity_at: at, draft_id: null });
const detail = (id: string, status: string) => ({ id, title: `Run ${id}`, status, purpose: 'revision', target_document_id: 'doc-1',
  agent_name: 'Ad-hoc agent', model_name: 'GPT', turns: 1, config: {}, document_draft: { id: `d-${id}`, document_id: 'doc-1', status: 'open' } });

function api(routes: Record<string, (body: any) => [number, unknown]>) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url}`;
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ key, body });
    if (!(key in routes)) throw new Error(`unexpected ${key}`);
    const [status, value] = routes[key](body);
    return new Response(JSON.stringify(value), { status });
  }));
  return calls;
}
const RUNS = 'GET /api/agent-sessions?document_id=doc-1';
const settingsOf = () => ({ options, error: '', settings: defaultSettings(options), update: vi.fn(), replace: vi.fn() });
function show(props: Partial<Parameters<typeof AgentPane>[0]> = {}) {
  const onDraftChange = vi.fn();
  render(<MemoryRouter><AgentPane documentId="doc-1" documentTitle="Runbook" settings={settingsOf()} initialRunId={null}
    confirmUnsaved={run => run()} onDraftChange={onDraftChange} {...props} /></MemoryRouter>);
  return { onDraftChange };
}
afterEach(() => { vi.unstubAllGlobals(); live.sessions = {}; live.requested = []; });

test('no open run: an empty conversation with the settings summary; the first message starts a run', async () => {
  const calls = api({ [RUNS]: () => [200, { sessions: [run('r0', 'closed')] }],
    'POST /api/agent-sessions': () => { live.sessions.r1 = detail('r1', 'starting'); return [201, detail('r1', 'starting')]; } });
  show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  expect(within(pane).getByText('Ask the agent to change this document.')).toBeInTheDocument();
  expect(within(pane).getByLabelText('Current settings')).toHaveTextContent('Claude · web-1, Payments · 2 tools');
  expect(within(pane).getByRole('button', { name: 'New chat' })).toBeDisabled();
  fireEvent.change(within(pane).getByLabelText('Message'), { target: { value: 'Add log rotation.' } });
  fireEvent.click(within(pane).getByRole('button', { name: 'Send message' }));
  await waitFor(() => expect(calls.find(c => c.key === 'POST /api/agent-sessions')?.body).toEqual({
    kind: 'revision', target: { document_id: 'doc-1' }, instruction: 'Add log rotation.', model_id: 'm2', prompt_id: 'p1',
    system_prompt: 'You revise carefully.', resources: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: [], collection_ids: [] },
    tools: ['ssh_run', 'get_service_info'], expert_ids: [], subagents: false }));
  await waitFor(() => expect(live.requested.at(-1)).toBe('r1'));
  expect(within(pane).getByLabelText('Message')).toHaveValue('');
});

test('the latest open run opens; messages go to it; its draft is reported', async () => {
  live.sessions.r2 = detail('r2', 'waiting');
  const calls = api({ [RUNS]: () => [200, { sessions: [run('r2', 'waiting'), run('r1', 'closed')] }],
    'POST /api/generations/sessions/r2/messages': () => [202, { working: true }] });
  const { onDraftChange } = show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  await waitFor(() => expect(live.requested.at(-1)).toBe('r2'));
  expect(onDraftChange).toHaveBeenLastCalledWith({ id: 'd-r2', document_id: 'doc-1', status: 'open' });
  fireEvent.change(within(pane).getByLabelText('Message'), { target: { value: 'Also mention cron.' } });
  fireEvent.click(within(pane).getByRole('button', { name: 'Send message' }));
  await waitFor(() => expect(calls.find(c => c.key === 'POST /api/generations/sessions/r2/messages')?.body).toEqual({ text: 'Also mention cron.' }));
  expect(calls.some(c => c.key === 'POST /api/agent-sessions')).toBe(false);
});

test('New chat closes the open run; while the agent works it asks first', async () => {
  live.sessions.r2 = detail('r2', 'working');
  const calls = api({ [RUNS]: () => [200, { sessions: [run('r2', 'working')] }],
    'POST /api/generations/sessions/r2/close': () => [200, { ...detail('r2', 'closed') }] });
  show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  await waitFor(() => expect(within(pane).getByRole('button', { name: 'New chat' })).toBeEnabled());
  fireEvent.click(within(pane).getByRole('button', { name: 'New chat' }));
  const dialog = await screen.findByRole('alertdialog', { name: 'Close this conversation?' });
  expect(dialog).toHaveTextContent('The agent is still working. Closing stops it.');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Keep it' }));
  expect(calls.some(c => c.key === 'POST /api/generations/sessions/r2/close')).toBe(false);
  fireEvent.click(within(pane).getByRole('button', { name: 'New chat' }));
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Close and start new' }));
  await waitFor(() => expect(calls.some(c => c.key === 'POST /api/generations/sessions/r2/close')).toBe(true));
  await waitFor(() => expect(live.requested.at(-1)).toBeNull());
  expect(within(pane).getByText('Ask the agent to change this document.')).toBeInTheDocument();
});

test('a refused start keeps the message and marks Settings', async () => {
  api({ [RUNS]: () => [200, { sessions: [] }],
    'POST /api/agent-sessions': () => [400, { error: { message: 'ssh_run needs at least one server.' } }] });
  show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  fireEvent.change(within(pane).getByLabelText('Message'), { target: { value: 'Add log rotation.' } });
  fireEvent.click(within(pane).getByRole('button', { name: 'Send message' }));
  expect(await within(pane).findByText('ssh_run needs at least one server.')).toBeInTheDocument();
  expect(within(pane).getByLabelText('Message')).toHaveValue('Add log rotation.');
  expect(within(pane).getByRole('button', { name: 'Settings (check the settings)' })).toBeInTheDocument();
});

test('a closed run blocks the message box and points to New chat', async () => {
  live.sessions.r3 = detail('r3', 'closed');
  api({ [RUNS]: () => [200, { sessions: [run('r3', 'closed')] }] });
  show({ initialRunId: 'r3' });
  expect(await screen.findByText('This conversation is closed. Start a New chat.')).toBeInTheDocument();
});

test('Earlier runs opens another run', async () => {
  live.sessions.r2 = detail('r2', 'waiting'); live.sessions.r1 = detail('r1', 'closed');
  api({ [RUNS]: () => [200, { sessions: [run('r2', 'waiting'), run('r1', 'closed', 'Remove the old steps.')] }] });
  show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  await waitFor(() => expect(live.requested.at(-1)).toBe('r2'));
  fireEvent.pointerDown(within(pane).getByRole('button', { name: 'Earlier runs' }), { button: 0, ctrlKey: false });
  fireEvent.click(await screen.findByRole('menuitem', { name: /Remove the old steps\./ }));
  await waitFor(() => expect(live.requested.at(-1)).toBe('r1'));
});

test('an unknown run shows the error and New chat clears it', async () => {
  api({ [RUNS]: () => [200, { sessions: [] }] });
  show({ initialRunId: 'nope' });
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  expect(await within(pane).findByText('Session not found')).toBeInTheDocument();
  fireEvent.click(within(pane).getByRole('button', { name: 'New chat' }));
  await waitFor(() => expect(live.requested.at(-1)).toBeNull());
  expect(within(pane).getByText('Ask the agent to change this document.')).toBeInTheDocument();
});

test('the conversation uses the compact workspace', async () => {
  api({ [RUNS]: () => [200, { sessions: [] }] });
  show();
  const pane = await screen.findByRole('region', { name: 'Ask agent' });
  expect(pane.querySelector('[data-compact="true"]')).not.toBeNull();
});
```

(Radix `DropdownMenu` opens on `pointerDown`; if the trigger needs `keyDown Enter` in this jsdom setup, use that and
say so. `act` is imported for the case a state update warning needs wrapping; remove it if unused.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/agentPane.test.tsx`
Expected: FAIL (cannot resolve `../AgentPane`).

- [ ] **Step 3: `RunWorkspace` compact mode and `savedIn`**

In `RunWorkspace.tsx`: add `compact?: boolean` to `RunWorkspaceProps` (doc comment: "In a side pane: no sidebar
column; Run details opens the drawer at every width"). When `compact`:
- the grid is a single column (`grid min-h-48 flex-1 gap-3` without `lg:grid-cols-[…]`);
- the `<aside aria-label="Run sidebar">` is not rendered;
- the Run details button drops `lg:hidden`.
Nothing else changes for existing callers (`compact` defaults to false).

Move `SAVE_TOOLS` and `savedIn` from `DocumentAgentRunView.tsx` to `lib/agentSessions.ts` (import `Turn` from
`./turns`), and in `DocumentAgentRunView.tsx` replace them with `export { savedIn } from '~/lib/agentSessions';` and an
import for its own use.

- [ ] **Step 4: Write `AgentPane.tsx`**

```tsx
/** The Ask agent pane on a document page: one conversation at a time (the latest open run, or none yet), in the compact
 * run workspace. The first message starts a run with the current settings; later messages go to it. Settings opens the
 * settings sheet (read-only while a run is open); New chat closes the run; Earlier runs opens another. */
export default function AgentPane({ documentId, documentTitle, settings, initialRunId, confirmUnsaved, onDraftChange, onRunChange }: {
  documentId: string; documentTitle: string; settings: ReturnType<typeof useAgentSettings>; initialRunId: string | null;
  confirmUnsaved: (run: () => void) => void; onDraftChange: (draft: SessionDetail['document_draft'] | null) => void;
  onRunChange?: (runId: string | null) => void }) {
  const [runs, setRuns] = useState<AgentRunSummary[] | null>(null);
  const [runId, setRunId] = useState<string | null>(initialRunId);
  const [chosen, setChosen] = useState(initialRunId !== null);  // the person (or the link) picked a run or New chat
  const run = useSessionEvents(runId);
  const { session } = run;
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState('');
  const [sheet, setSheet] = useState(false);
  const [confirmNew, setConfirmNew] = useState(false);
  const { notice, clear, stopping, sent } = useSessionNotice(session?.status);
  const fill = useFillViewport<HTMLDivElement>();

  // The run list: on mount and after a start or a close. Until the person picks, the pane opens the latest open run.
  const [listed, setListed] = useState(0);
  useEffect(() => { /* abortable getDocumentAgentRuns(documentId) → setRuns(value.sessions);
     if (!chosen) setRunId(value.sessions.find(item => item.status !== 'closed')?.id ?? null) */ }, [documentId, listed]);
  useEffect(() => { onDraftChange(session?.document_draft ?? null); }, [session?.document_draft?.id, session?.document_draft?.status]);
  useEffect(() => { onRunChange?.(runId); }, [runId]);

  const open = session !== null && session.status !== 'closed';
  function pick(id: string | null) { setChosen(true); setRunId(id); setMessage(''); setRefused(''); run.setError(''); clear(); }
  async function send() {
    const text = message.trim();
    if (!text || !settings.options || !settings.settings) return;
    if (open) { /* act: const result = await sendMessage(runId!, text); setMessage(''); sent(result.working); */ return; }
    confirmUnsaved(() => void (async () => {
      setBusy(true); setRefused('');
      try { const started = await startAgentSession(startInput(documentId, settings.options!, settings.settings!, text));
        setMessage(''); pick(started.id); setMessage(''); setListed(n => n + 1); }
      catch (e) { setRefused(e instanceof Error ? e.message : 'The run could not start.'); }
      finally { setBusy(false); }
    })());
  }
  async function closeAndClear() { /* if runId && session && status !== 'closed': await closeSession(runId);
     pick(null); setListed(n => n + 1) — errors to run.setError */ }
  function newChat() { if (session?.status === 'working') setConfirmNew(true); else void closeAndClear(); }
  // … markup:
  // <section aria-label="Ask agent" ref={fill.ref} style={fill.style} className="flex min-w-0 flex-col gap-2">
  //  <RunWorkspace compact run={run} agentName={session?.agent_name ?? 'Ask agent'} resources="Per run" notice={notice}
  //   heading={<div className="flex flex-col gap-1"><h2 className="break-words font-semibold">{session?.title ?? 'New conversation'}</h2>
  //     {!session && settings.options && settings.settings && <p aria-label="Current settings" className="text-xs text-muted-foreground">
  //       {settingsSummary(settings.options, settings.settings)}</p>}
  //     {refused && <Alert variant="destructive"><AlertDescription>{refused}</AlertDescription></Alert>}
  //     {settings.error && <Alert variant="destructive"><AlertDescription>{settings.error}</AlertDescription></Alert>}</div>}
  //   headerActions={<>
  //     <Button size="sm" variant="outline" aria-label={refused ? 'Settings (check the settings)' : 'Settings'} disabled={!settings.options}
  //       onClick={() => setSheet(true)}><Settings2 data-icon="inline-start" />Settings{refused && <span aria-hidden className="ml-1 size-2 rounded-full bg-destructive" />}</Button>
  //     <Button size="sm" variant="outline" disabled={busy || (runId === null && !run.error)} onClick={newChat}>New chat</Button>
  //     <DropdownMenu><DropdownMenuTrigger asChild><Button size="sm" variant="ghost" disabled={!runs || runs.filter(r => r.id !== runId).length === 0}>Earlier runs</Button></DropdownMenuTrigger>
  //       <DropdownMenuContent align="end">{runs?.filter(r => r.id !== runId).map(r => <DropdownMenuItem key={r.id} onSelect={() => pick(r.id)}>
  //         {`${r.title} · ${statusLabels[r.status]}`}</DropdownMenuItem>)}</DropdownMenuContent></DropdownMenu></>}
  //   answerActions={turn => <>{!savedIn(turn) && <span className="text-xs text-muted-foreground">No draft saved in this turn</span>}
  //     {session && <Button variant="ghost" size="sm" asChild><Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link></Button>}</>}
  //   empty={<p className="text-sm text-muted-foreground">Ask the agent to change this document.</p>}
  //   composer={{ value: message, onChange: setMessage, busy: busy || !settings.settings, label: 'Message',
  //     placeholder: 'For example: add a section on log rotation', onSend: () => void send(),
  //     onStop: open ? () => void act(async () => { await stopSession(runId!); stopping(); }) : undefined,
  //     blockedReason: session?.status === 'closed' ? 'This conversation is closed. Start a New chat.' : null }} />
  //  {settings.options && settings.settings && <AgentSettingsSheet open={sheet} onOpenChange={setSheet} documentId={documentId}
  //    documentTitle={documentTitle} options={settings.options} settings={settings.settings} onChange={settings.update}
  //    previewMessage={message} session={open ? session : null}
  //    onStartNew={() => { const next = settingsFromSession(settings.options!, session!); void closeAndClear().then(() => settings.replace(next)); }} />}
  //  AlertDialog open={confirmNew}: title "Close this conversation?", description "The agent is still working. Closing stops it.",
  //    Cancel "Keep it", Action "Close and start new" → void closeAndClear().
  // </section>
}
```

Write the component in full from that outline (imports: `Settings2` from `lucide-react`, `DropdownMenu*` from
`~/components/ui/dropdown-menu`, `AlertDialog*`, `Alert`, `Button`, `Link`, `statusLabels`, `runPath`, `savedIn`,
`useFillViewport`). Keep `act(action)` as in `DocumentAgentRunView.tsx` (busy, errors to `run.setError`). `RunWorkspace`
needs `run` even without a session: `useSessionEvents(null)` returns an empty run.

- [ ] **Step 5: Run the tests**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/agentPane.test.tsx app/components/__tests__/documentAgentRun.test.tsx app/components/__tests__/agentSessions.test.tsx && npx tsc --noEmit`
Expected: PASS (the existing run-page and Test-tab workspace tests still pass with `compact` off).

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui/app/components/RunWorkspace.tsx services/admin-ui/app/lib/agentSessions.ts \
  services/admin-ui/app/components/DocumentAgentRunView.tsx services/admin-ui/app/components/AgentPane.tsx \
  services/admin-ui/app/components/__tests__/agentPane.test.tsx
git commit -m "feat(admin-ui): Ask agent chat pane — one conversation per document, settings sheet, New chat, Earlier runs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The split document page, the draft bar and the deep link

**Files:**
- Modify: `services/admin-ui/app/components/DocumentEditorView.tsx` (split layout, pane toggle, divider, draft bar;
  `Editor` hands its unsaved-changes confirmation up; the Ask agent button toggles the pane)
- Create: `services/admin-ui/app/components/AgentDraftBar.tsx`
- Modify: `services/admin-ui/app/routes/documentAgentRun.tsx` (renders the document page; the pane opens on `runId`)
- Delete: `services/admin-ui/app/components/AskAgentSheet.tsx`, `services/admin-ui/app/components/__tests__/askAgent.test.tsx`,
  `services/admin-ui/app/components/DocumentAgentRunView.tsx`
- Modify: `services/admin-ui/app/components/__tests__/documentAgentRun.test.tsx` (keep the `savedIn`, Agent runs tab and
  `DraftAuthor` tests, importing `savedIn` from `~/lib/agentSessions`; remove the two run-page tests, which Task 3's
  page tests replace)
- Modify: `services/admin-ui/app/components/GenerationView.tsx` only if it imports `DocumentAgentRunView` (it should
  import `agentRunPath`, which stays)
- Create: `services/admin-ui/app/components/__tests__/documentAgentPane.test.tsx`
- Modify: `docs/user-guide.md` (`#### Ask agent`), `docs/documentation.md` (the Ask agent paragraph: "a chat pane on
  the document page")

**Interfaces:**
- Consumes: Task 1 `useAgentSettings`; Task 2 `AgentPane` (props above); `lib/drafts` (`getDraft`, `promoteDraft`,
  `STALE_BASE`, `promotedNotice`, `PromoteResult`, `draftPath`); `ApiError` from `lib/api`.
- Produces: `AgentDraftBar({ draft, confirm, onPromoted })` — `draft: NonNullable<SessionDetail['document_draft']>`,
  `confirm: (run: () => void) => void`, `onPromoted: (result: PromoteResult) => void`.

- [ ] **Step 1: Write the failing page test**

`services/admin-ui/app/components/__tests__/documentAgentPane.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import DocumentEditorView from '../DocumentEditorView';

vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const pane = vi.hoisted(() => ({ mounts: 0, props: [] as any[] }));
vi.mock('../AgentPane', async () => {
  const { useEffect } = await import('react');
  return { default: (props: any) => {
    useEffect(() => { pane.mounts += 1; }, []);
    pane.props.push(props);
    return <section aria-label="Ask agent">
      <p>{`run ${props.initialRunId ?? 'none'}`}</p>
      <button type="button" onClick={() => props.confirmUnsaved(() => { pane.props.push({ started: true }); })}>Send message</button>
      <button type="button" onClick={() => props.onDraftChange({ id: 'd1', document_id: 'doc-1', status: 'open' })}>Agent saved</button>
    </section>;
  } };
});
const NativeRequest = globalThis.Request;
beforeEach(() => { vi.stubGlobal('Request', class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
}); try { localStorage.clear(); } catch { /* none */ } pane.mounts = 0; pane.props = []; });
afterEach(() => vi.unstubAllGlobals());

const at = '2026-10-12T10:00:00Z';
const resources = { code_projects: [], services: [], servers: [], deployments: [], environments: [] };
const version = { id: 'v2', version: 2, title: 'Runbook', description: '', purpose: 'runbook', audience: 'both', tags: [],
  markdown: '# Runbook', content_hash: 'a'.repeat(64), source_snapshot: resources, origin: 'manual', base_version_id: 'v1', created_at: at };
const document = { id: 'doc-1', slug: 'runbook', title: 'Runbook', description: '', purpose: 'runbook', audience: 'both', tags: [],
  code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [], environment_ids: [], revision: 2, version: 2,
  current_version_id: 'v2', published_version_id: null, content_hash: 'a', created_at: at, updated_at: at, current_version: version,
  versions: [{ ...version, from_draft: false }] };
const draft = { id: 'd1', document_id: 'doc-1', slug: null, folder: null, title: 'Runbook', origin: 'agent', session_id: 'r1', session_title: 'x',
  session_purpose: 'revision', author: null, edited: false, status: 'open', revision: 3, base_version_id: 'v1', base_version: 1, live_version: 2,
  stale: true, created_at: at, updated_at: at, description: '', purpose: 'runbook', audience: 'both', tags: [], markdown: '# New',
  resource_ids: {}, live_markdown: '# Runbook', document_title: 'Runbook', promoted_version_id: null, decided_at: null, sources: [] };
const STALE = 'This draft was based on version 1; the live version is now 2.';
const base: Record<string, (body: any) => [number, unknown]> = {
  'GET /api/documentation/documents/doc-1': () => [200, document],
  'GET /api/documentation/resources': () => [200, resources],
  'GET /api/agent-sessions/options?kind=revision&document_id=doc-1': () => [200, { models: [], default_model_id: null, prompts: [],
    default_prompt: { id: null, name: 'Built-in', content: 'x' }, resources: { servers: [], services: [], environments: [], code_projects: [], collections: [] },
    tools: [], fixed_tools: [], experts: [] }],
  'GET /api/documentation/drafts/d1': () => [200, draft],
};
function api(routes: Record<string, (body: any) => [number, unknown]>) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url}`;
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ key, body });
    if (!(key in routes)) throw new Error(`unexpected ${key}`);
    const [status, value] = routes[key](body);
    return new Response(JSON.stringify(value), { status });
  }));
  return calls;
}
function show(url: string) {
  const router = createMemoryRouter([{ path: '/documents/:documentId/agent/:runId', element: <DocumentEditorView /> },
    { path: '/documents/:documentId', element: <DocumentEditorView /> }], { initialEntries: [url] });
  render(<RouterProvider router={router} />);
  return router;
}

test('Ask agent toggles the pane, and the choice is remembered', async () => {
  api(base);
  show('/documents/doc-1');
  const toggle = await screen.findByRole('button', { name: 'Ask agent' });
  expect(toggle).toHaveAttribute('aria-pressed', 'false');
  expect(screen.queryByRole('region', { name: 'Ask agent' })).toBeNull();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('region', { name: 'Ask agent' })).toBeInTheDocument();
  expect(screen.getByRole('separator', { name: 'Resize the agent pane' })).toBeInTheDocument();
  expect(localStorage.getItem('ask-agent-pane')).toBe('open');
});

test('the run deep link opens the pane on that run', async () => {
  api(base);
  show('/documents/doc-1/agent/r7');
  expect(await screen.findByText('run r7')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Ask agent' })).toHaveAttribute('aria-pressed', 'true');
});

test('the first message asks about unsaved changes first', async () => {
  api(base);
  show('/documents/doc-1/agent/r7');
  fireEvent.change(await screen.findByLabelText('Markdown'), { target: { value: '# Edited, not saved' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  const dialog = await screen.findByRole('alertdialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Keep editing' }));
  expect(pane.props.some(p => p.started)).toBe(false);
  expect(screen.getByLabelText('Markdown')).toHaveValue('# Edited, not saved');
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Discard changes' }));
  expect(pane.props.some(p => p.started)).toBe(true);
});

test('the draft bar shows the agent draft; Compare opens it; a stale Promote asks, then promotes', async () => {
  let promoted = 0;
  const calls = api({ ...base,
    'POST /api/documentation/drafts/d1/promote': body => { promoted += 1;
      return body.confirm_stale ? [200, { draft: { ...draft, status: 'promoted' }, document_id: 'doc-1', version_id: 'v3', revision: 3, export_error: null }]
        : [409, { error: { message: STALE, code: 'STALE_BASE' } }]; },
    'GET /api/documentation/documents/doc-1/drafts': () => [200, { drafts: [draft] }] });
  const router = show('/documents/doc-1/agent/r1');
  fireEvent.click(await screen.findByRole('button', { name: 'Agent saved' }));
  const bar = await screen.findByRole('region', { name: 'Agent draft' });
  expect(await within(bar).findByText('Agent draft · based on version 1')).toBeInTheDocument();
  expect(within(bar).getByText('stale')).toBeInTheDocument();
  fireEvent.click(within(bar).getByRole('button', { name: 'Promote' }));
  expect(await within(bar).findByText(STALE)).toBeInTheDocument();
  fireEvent.click(within(bar).getByRole('button', { name: 'Promote anyway' }));
  await waitFor(() => expect(promoted).toBe(2));
  expect(calls.filter(c => c.key === 'POST /api/documentation/drafts/d1/promote').map(c => c.body.confirm_stale)).toEqual([false, true]);
  expect(await screen.findByText('Draft promoted to version 3.')).toBeInTheDocument();
  expect(pane.mounts).toBe(1);  // the pane keeps its conversation when a draft is promoted
  fireEvent.click(screen.getByRole('button', { name: 'Agent saved' }));
  fireEvent.click(within(await screen.findByRole('region', { name: 'Agent draft' })).getByRole('button', { name: 'Compare' }));
  await waitFor(() => expect(router.state.location.search).toBe('?tab=drafts&draft=d1'));
});
```

Notes for the implementer:
- Check the promote request body key in `lib/drafts.ts` `promoteDraft` (`confirm_stale` assumed) and the STALE error
  shape (`code: 'STALE_BASE'` as `ApiError.code`); align the test to the real shapes and say so.
- The test name `the pane keeps its conversation when a draft is promoted` is the `pane.mounts` assertion inside the
  last test (Review Focus 1); keep that assertion.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/documentAgentPane.test.tsx`
Expected: FAIL (no pane, no separator, no draft bar).

- [ ] **Step 3: Write `AgentDraftBar.tsx`**

```tsx
/** Above the editor while the Ask agent pane's run has an open draft: its base version and stale badge, Compare (the
 * Drafts tab with that draft) and Promote (with the stale check: the server's message, then Promote anyway). */
export default function AgentDraftBar({ draft, confirm, onPromoted }: { draft: NonNullable<SessionDetail['document_draft']>;
  confirm: (run: () => void) => void; onPromoted: (result: PromoteResult) => void }) {
  const [, setParams] = useSearchParams();
  const [detail, setDetail] = useState<DraftDetail | null>(null);
  const [stale, setStale] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { /* abortable getDraft(draft.id) → setDetail; failure → setError('Could not load the agent draft.') */ }, [draft.id, draft.status]);
  async function promote(confirmStale: boolean) {
    if (!detail) return;
    setBusy(true); setError('');
    try { onPromoted(await promoteDraft(detail.id, detail.revision, confirmStale)); setStale(''); }
    catch (e) { if (e instanceof ApiError && e.code === STALE_BASE) setStale(e.message); else setError(e instanceof Error ? e.message : 'Could not promote the draft.'); }
    finally { setBusy(false); }
  }
  if (draft.status !== 'open') return null;
  // <section aria-label="Agent draft" className="flex flex-col gap-2 rounded-lg border bg-muted/40 p-3">
  //  <div className="flex flex-wrap items-center gap-2 text-sm">
  //   <span>{detail ? `Agent draft · based on version ${detail.base_version ?? '?'}` : 'Agent draft'}</span>
  //   {detail?.stale && <Badge variant="destructive">stale</Badge>}
  //   <span className="ml-auto flex gap-2">
  //    <Button type="button" size="sm" variant="outline" onClick={() => setParams({ tab: 'drafts', draft: draft.id }, { replace: true })}>Compare</Button>
  //    <Button type="button" size="sm" disabled={busy || !detail || stale !== ''} onClick={() => confirm(() => void promote(false))}>Promote</Button></span></div>
  //  {stale && <Alert variant="destructive"><AlertDescription className="flex flex-col gap-2"><span>{stale}</span><span className="flex gap-2">
  //    <Button size="sm" variant="destructive" disabled={busy} onClick={() => void promote(true)}>Promote anyway</Button>
  //    <Button size="sm" variant="outline" disabled={busy} onClick={() => setStale('')}>Cancel</Button></span></AlertDescription></Alert>}
  //  {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
  // </section>
}
```

Write it in full from the outline.

- [ ] **Step 4: The split page**

In `DocumentEditorView.tsx`:

1. `Editor` gets two props: `agentOpen: boolean`, `onToggleAgent: () => void`, and `registerConfirm: (confirm: (run: () => void) => void) => void`.
   - `useEffect(() => { registerConfirm(unlessUnsaved); });` (every render, so the parent always holds the current
     `dirty`).
   - The Ask agent button becomes
     `<Button type="button" variant="outline" aria-pressed={agentOpen} onClick={onToggleAgent}><Bot data-icon="inline-start" />Ask agent</Button>`
     (only when `document`); remove `asking`, the `AskAgentSheet` import and its render.
2. `DocumentEditorView` (the default export):
   ```tsx
   const { documentId, runId } = useParams();
   const [agentOpen, setAgentOpen] = useState(() => runId !== undefined || readPane() === 'open');
   const [width, setWidth] = useState(readWidth);              // the pane's percent, 25–60, default 40
   const [agentDraft, setAgentDraft] = useState<SessionDetail['document_draft'] | null>(null);
   const confirmRef = useRef<(run: () => void) => void>((run) => run());
   const settings = useAgentSettings(agentOpen && documentId ? documentId : null);  // loads only while the pane is open
   ```
   - `readPane`/`readWidth`/writes: `localStorage` `ask-agent-pane` and `ask-agent-pane-width`, each in try/catch;
     toggling writes `'open'`/`'closed'`.
   - `onPromoted` (existing handler) is shared by the Drafts tab and the bar.
   - Layout when `loaded?.document` and `agentOpen` (wide screens, `lg:`):
     ```tsx
     <div className="flex min-w-0 flex-col gap-4 lg:flex-row lg:items-start">
       <div className="min-w-0 lg:flex-1" style={{ flexBasis: `${100 - width}%` }}>
         {agentDraft && <AgentDraftBar draft={agentDraft} confirm={run => confirmRef.current(run)} onPromoted={onPromoted} />}
         <Editor … />
       </div>
       <div role="separator" aria-orientation="vertical" aria-label="Resize the agent pane" aria-valuemin={25} aria-valuemax={60}
         aria-valuenow={width} tabIndex={0} className="hidden w-1.5 cursor-col-resize self-stretch rounded bg-border lg:block"
         onPointerDown={startDrag} onKeyDown={e => { if (e.key === 'ArrowLeft') resize(width + 5); if (e.key === 'ArrowRight') resize(width - 5); }} />
       <div className="min-w-0 lg:sticky lg:top-4" style={{ flexBasis: `${width}%` }}>
         <AgentPane documentId={documentId} documentTitle={loaded.document.title} settings={settings} initialRunId={runId ?? null}
           confirmUnsaved={run => confirmRef.current(run)} onDraftChange={setAgentDraft} />
       </div>
     </div>
     ```
     `resize(n)` clamps to 25–60 and saves; `startDrag` follows `pointermove` on `window` (pane percent =
     `100 - (clientX - left) / containerWidth * 100`), stops on `pointerup`. On narrow screens the pane renders below
     the editor at full width (the `flex-col` default); the spec's full-height sheet on narrow screens is met by the
     pane's own `useFillViewport` height. Without the pane, the layout is today's.
   - The `AgentPane` element must not be inside `Editor` and must keep a stable position in the tree across the
     editor's `refresh` remount (Review Focus 1): give it `key={documentId}` only.
   - When `agentOpen` is false, `agentDraft` is cleared (`setAgentDraft(null)`), and no pane renders.
3. `routes/documentAgentRun.tsx`:
   ```tsx
   import AdminLayout from '../components/AdminLayout';
   import DocumentEditorView from '../components/DocumentEditorView';

   /** An Ask agent run's link: the document page with the agent pane open on that run. */
   export default function Page() {
     return <AdminLayout><DocumentEditorView /></AdminLayout>;
   }
   ```
4. Delete `AskAgentSheet.tsx`, `__tests__/askAgent.test.tsx` and `DocumentAgentRunView.tsx`; update
   `__tests__/documentAgentRun.test.tsx` as listed under Files (import `savedIn` from `~/lib/agentSessions`; delete the
   run-page and redirect tests). `grep -rn "DocumentAgentRunView\|AskAgentSheet" app` must print nothing.

- [ ] **Step 5: Docs**

`docs/user-guide.md`, replace the body of `#### Ask agent` with:

```markdown
**Ask agent** on a document's page opens a chat beside the document. Write what you want ("Add a section on log
rotation; remove the old backup steps") and send it: the agent starts with the current settings, reads the document
part by part and saves its changes as a draft — never the live version. Its replies are a normal conversation: it may
ask you something first, and you can ask for more changes; each save updates the same draft. A bar above the editor
shows the agent's draft (Compare opens it with the changes; Promote makes it the live version). A turn that ended
without a save says so.

**Settings** in the chat opens what the agent may use: the model, the servers and other resources (the ones linked to
the document are ticked; a server whose SSH is not set up or not allowed cannot be chosen), the tools (reading and
saving the document are always on), subagents and experts (off unless you turn them on), and under **Advanced** the
system prompt (prompts tagged `document-revision` in Prompts, or the built-in one). **Preview** shows exactly what the
next message will start with, as JSON. Your choices are remembered for the document in this browser. While a
conversation is open its settings are fixed: Settings shows them, and **Start new chat with these settings** closes it
and lets you change them for the next one. **New chat** closes the conversation; **Earlier runs** reopens another. The
**Agent runs** tab lists the document's runs, open ones first. Deleting the document deletes its runs; their drafts are
deleted with it.
```

`docs/documentation.md`: in the Ask agent paragraph, say the runs are started from the chat pane on the document page
(the first message is the instruction) and that `/documents/{id}/agent/{run}` opens the page with the pane on that run.

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-ui && npx vitest run && npx tsc --noEmit && npx react-router routes >/dev/null`
Expected: all tests pass, no type errors, routes resolve.

- [ ] **Step 7: Commit**

```bash
git add -A services/admin-ui/app docs/user-guide.md docs/documentation.md
git status --short   # check: no node_modules, no build, no .superpowers
git commit -m "feat(admin-ui): the document page splits into the document and the Ask agent pane; agent draft bar; run links open the pane

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Self-review

- Spec coverage: settings defaults, remembered per document, dropped stale ids, read-only for an open run, Start new chat
  with these settings, Preview with the message box text or placeholder (Task 1); one conversation, first message
  starts a run, follow-ups, New chat with the working confirmation, Earlier runs, closed run, refused start with the
  Settings dot, compact workspace, "No draft saved in this turn" (Task 2); split layout with toggle, remembered state
  and divider, draft bar with Compare/Promote/stale, deep link, unsaved-changes confirmation, removal of the old sheet
  and run page, docs (Task 3).
- Review Focus items each have a named test in the owning task.
- Names used across tasks: `AgentSettings`, `BUILT_IN`, `useAgentSettings`, `startInput`, `settingsSummary`,
  `settingsFromSession`, `shownTools`, `AgentSettingsSheet`, `AgentPane`, `savedIn`, `AgentDraftBar`, `compact`.
