import { OWN, defaultSettings, restoreSettings, settingsFromSession, settingsSummary, shownTools, startInput } from '../agentSettings';
import type { SessionDetail } from '../sessions';
import { options } from './agentOptionsFixture';

test('defaults: the default model, the first tagged prompt, the usable linked resources, every tool on', () => {
  const settings = defaultSettings(options);
  expect(settings).toEqual({ modelId: 'm2', promptKey: 'p2', systemPrompt: options.prompts[1].content,
    chosen: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: [], collection_ids: [] },
    off: [], subagents: false, experts: [] });
  expect(shownTools(options, settings).map(tool => tool.name)).toEqual(['ssh_run', 'save_document', 'get_document', 'get_service_info']);
});

test('startInput sends only the shown tools that are on', () => {
  const settings = { ...defaultSettings(options), off: ['get_service_info', 'get_document', 'save_document'] };
  expect(startInput('doc-1', 'Runbook', options, settings, 'x').tools).toEqual(['ssh_run']);
  expect(startInput('doc-1', 'Runbook', options, { ...settings, promptKey: OWN }, 'x').prompt_id).toBeNull();
});

test('the start body gives the document as an editable resource and the context names it', () => {
  const body = startInput('doc-1', 'Runbook', options, defaultSettings(options), '  Add a section.  ');
  expect(body).toMatchObject({ started_from: { document_id: 'doc-1' }, message: 'Add a section.', prompt_id: 'p2',
    system_prompt: options.prompts[1].content, context: 'Document: Runbook (id doc-1)' });
  expect(body.resources.documents).toEqual([{ id: 'doc-1', editable: true }]);
  expect(body.tools).toEqual(expect.arrayContaining(['save_document', 'get_document']));
  expect(body).not.toHaveProperty('kind');
});

test('settings saved before the update are dropped and rebuilt', () => {
  const old = { v: 1, linked: ['s1'], settings: { modelId: 'm1', promptKey: 'built-in', systemPrompt: 'Old', off: ['ssh_run'] } };
  expect(restoreSettings(options, old)).toEqual(defaultSettings(options));
});

test('with no library prompts the person writes their own', () => {
  const none = { ...options, prompts: [], default_prompt_id: null };
  expect(defaultSettings(none)).toMatchObject({ promptKey: OWN, systemPrompt: '' });
});

test('restored settings drop what the options no longer have', () => {
  const linked = ['s1', 's2', 'v1'];
  const saved = { v: 2, linked, settings: { modelId: 'gone', promptKey: 'gone', systemPrompt: 'Mine.', subagents: true,
    chosen: { server_ids: ['s1', 's9'], service_ids: [], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] },
    off: ['ssh_run', 'gone_tool'], experts: ['e1', 'e9'] } };
  expect(restoreSettings(options, saved)).toEqual({ modelId: 'm2', promptKey: 'p2', systemPrompt: 'Mine.', subagents: true,
    chosen: { server_ids: ['s1'], service_ids: [], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] },
    off: ['ssh_run'], experts: ['e1'] });
  // the document's links changed since: the resources start again from the links
  expect(restoreSettings(options, { ...saved, linked: ['s1'] }).chosen).toEqual(defaultSettings(options).chosen);
  expect(restoreSettings(options, 'garbage')).toEqual(defaultSettings(options));
  });

test('settingsFromSession rebuilds the settings a run started with', () => {
  const session = { model_id: 'm1', prompt_snapshot: { system_prompt: 'Run prompt.', prompt_id: null },
    run_resources: { server_ids: ['s1'], service_ids: [], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] },
    config: { tools: { items: [{ tool: 'ssh_run', mode: 'on' }, { tool: 'read_file', mode: 'off' }, { tool: 'get_service_info', mode: 'on' },
      { tool: 'run_subagent', mode: 'on' }, { tool: 'get_doc', mode: 'on' }] }, experts: [{ id: 'e1', name: 'Payments expert' }] },
  } as unknown as SessionDetail;
  expect(settingsFromSession(options, session)).toEqual({ modelId: 'm1', promptKey: OWN, systemPrompt: 'Run prompt.',
    chosen: { server_ids: ['s1'], service_ids: [], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] },
    off: ['read_file'], subagents: true, experts: ['e1'] });
});

test('settingsFromSession leaves out what today\'s options no longer have or cannot use', () => {
  const session = { model_id: 'gone-model', prompt_snapshot: { system_prompt: 'Run prompt.', prompt_id: null },
    run_resources: { server_ids: ['s1', 's2', 's9'], service_ids: ['v1'], environment_ids: [], code_project_ids: ['c9'], collection_ids: [] },
    config: { tools: { items: [{ tool: 'ssh_run', mode: 'off' }, { tool: 'gone_tool', mode: 'off' }] }, experts: [{ id: 'e1' }, { id: 'e9' }] },
  } as unknown as SessionDetail;
  expect(settingsFromSession(options, session)).toEqual({ modelId: 'm2', promptKey: OWN, systemPrompt: 'Run prompt.',
    chosen: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: [], collection_ids: [] },
    off: ['ssh_run'], subagents: false, experts: ['e1'] });
});

test('the summary names the model, up to three resources and the tools on', () => {
  expect(settingsSummary(options, defaultSettings(options))).toBe('Claude · web-1, Payments · 4 tools');
  const many = { ...defaultSettings(options), chosen: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] } };
  expect(settingsSummary(options, { ...many, chosen: { ...many.chosen, server_ids: ['s1', 's2'] } })).toBe('Claude · web-1, prod-1, Payments +1 · 5 tools');
  expect(settingsSummary(options, { ...defaultSettings(options), chosen: { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] } }))
    .toBe('Claude · no resources · 3 tools');
});

test('switching the document never writes the first document\'s settings under the second\'s key', async () => {
  const { renderHook, waitFor, act } = await import('@testing-library/react');
  const { useAgentSettings } = await import('../agentSettings');
  const store = new Map<string, string>();
  const writes: [string, string][] = [];
  vi.stubGlobal('localStorage', { getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => { writes.push([k, v]); store.set(k, v); }, removeItem: (k: string) => store.delete(k) });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url.includes('doc-2') ? { ...options, default_model_id: 'm1' } : options), { status: 200 })));
  const { result, rerender } = renderHook(({ id }) => useAgentSettings(id), { initialProps: { id: 'doc-1' } });
  await waitFor(() => expect(result.current.settings?.modelId).toBe('m2'));
  act(() => result.current.update({ systemPrompt: 'Doc one prompt.' }));
  await waitFor(() => expect(writes.some(([k, v]) => k === 'ask-agent-settings:doc-1' && v.includes('Doc one prompt.'))).toBe(true));
  rerender({ id: 'doc-2' });
  expect(result.current.options).toBeNull();
  expect(result.current.settings).toBeNull();
  await waitFor(() => expect(result.current.settings?.modelId).toBe('m1'));
  expect(result.current.settings?.systemPrompt).toBe(options.prompts[1].content);
  for (const [key, value] of writes) if (key === 'ask-agent-settings:doc-2') expect(value).not.toContain('Doc one prompt.');
  vi.unstubAllGlobals();
});
