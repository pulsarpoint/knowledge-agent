import { BUILT_IN, defaultSettings, restoreSettings, settingsFromSession, settingsSummary, shownTools, startInput } from '../agentSettings';
import type { SessionDetail } from '../sessions';
import { options } from './agentOptionsFixture';

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

test('settingsFromSession leaves out what today\'s options no longer have or cannot use', () => {
  const session = { model_id: 'gone-model', prompt_snapshot: { system_prompt: 'Run prompt.', prompt_id: null },
    run_resources: { server_ids: ['s1', 's2', 's9'], service_ids: ['v1'], environment_ids: [], code_project_ids: ['c9'], collection_ids: [] },
    config: { tools: { items: [{ tool: 'ssh_run', mode: 'off' }, { tool: 'gone_tool', mode: 'off' }] }, experts: [{ id: 'e1' }, { id: 'e9' }] },
  } as unknown as SessionDetail;
  expect(settingsFromSession(options, session)).toEqual({ modelId: 'm2', promptKey: BUILT_IN, systemPrompt: 'Run prompt.',
    chosen: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: [], collection_ids: [] },
    off: ['ssh_run'], subagents: false, experts: ['e1'] });
});

test('the summary names the model, up to three resources and the tools on', () => {
  expect(settingsSummary(options, defaultSettings(options))).toBe('Claude · web-1, Payments · 2 tools');
  const many = { ...defaultSettings(options), chosen: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] } };
  expect(settingsSummary(options, { ...many, chosen: { ...many.chosen, server_ids: ['s1', 's2'] } })).toBe('Claude · web-1, prod-1, Payments +1 · 3 tools');
  expect(settingsSummary(options, { ...defaultSettings(options), chosen: { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] } }))
    .toBe('Claude · no resources · 1 tool');
});

test('switching the document never writes the first document\'s settings under the second\'s key', async () => {
  const { renderHook, waitFor, act } = await import('@testing-library/react');
  const { useAgentSettings } = await import('../agentSettings');
  const store = new Map<string, string>();
  const writes: [string, string][] = [];
  vi.stubGlobal('localStorage', { getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => { writes.push([k, v]); store.set(k, v); }, removeItem: (k: string) => store.delete(k) });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url.includes('document_id=doc-2') ? { ...options, default_model_id: 'm1' } : options), { status: 200 })));
  const { result, rerender } = renderHook(({ id }) => useAgentSettings(id), { initialProps: { id: 'doc-1' } });
  await waitFor(() => expect(result.current.settings?.modelId).toBe('m2'));
  act(() => result.current.update({ systemPrompt: 'Doc one prompt.' }));
  await waitFor(() => expect(writes.some(([k, v]) => k === 'ask-agent-settings:doc-1' && v.includes('Doc one prompt.'))).toBe(true));
  rerender({ id: 'doc-2' });
  expect(result.current.options).toBeNull();
  expect(result.current.settings).toBeNull();
  await waitFor(() => expect(result.current.settings?.modelId).toBe('m1'));
  expect(result.current.settings?.systemPrompt).toBe('You revise carefully.');
  for (const [key, value] of writes) if (key === 'ask-agent-settings:doc-2') expect(value).not.toContain('Doc one prompt.');
  vi.unstubAllGlobals();
});
