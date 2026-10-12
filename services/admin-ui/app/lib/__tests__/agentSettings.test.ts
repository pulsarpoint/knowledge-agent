import { BUILT_IN, defaultSettings, restoreSettings, settingsFromSession, settingsSummary, shownTools, startInput } from '../agentSettings';
import type { SessionDetail } from '../generations';
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

test('the summary names the model, up to three resources and the tools on', () => {
  expect(settingsSummary(options, defaultSettings(options))).toBe('Claude · web-1, Payments · 2 tools');
  const many = { ...defaultSettings(options), chosen: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: ['c1'], collection_ids: [] } };
  expect(settingsSummary(options, { ...many, chosen: { ...many.chosen, server_ids: ['s1', 's2'] } })).toBe('Claude · web-1, prod-1, Payments +1 · 3 tools');
  expect(settingsSummary(options, { ...defaultSettings(options), chosen: { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] } }))
    .toBe('Claude · no resources · 1 tool');
});
