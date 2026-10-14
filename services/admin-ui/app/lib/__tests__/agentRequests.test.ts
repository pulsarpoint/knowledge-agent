import { afterEach, expect, test, vi } from 'vitest';
import { saveAgent, type AgentProfile } from '~/features/agents/api';
import { impliedTools, saveAgentTools, type AgentTool, type CatalogTool } from '~/features/agents/tools';

afterEach(() => vi.unstubAllGlobals());
const sent = () => JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body));
const lead = { id: 'lead', name: 'Lead', description: '', when_to_use: '', enabled: true, revision: 3 } as AgentProfile;

test('a tools save sends what a subagent may use', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ revision: 5, tools: [] })));
  await saveAgentTools('lead', 4, [{ tool: 'read_file', mode: 'on', usage: null, subagent_allowed: false } as AgentTool]);
  expect(sent()).toEqual({ expected_revision: 4, tools: [{ tool: 'read_file', mode: 'on', usage: null, subagent_allowed: false }] });
});

test('General sends May start subagents', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(lead)));
  await saveAgent({ ...lead, subagents: true });
  expect(sent().subagents).toBe(true);
});

test('new agents never get run_subagent from their capabilities', () => {
  const catalog: CatalogTool[] = [
    { name: 'read_file', group: 'code', label: 'Read file', summary: '', usage: '', needs: 'files' },
    { name: 'run_subagent', group: 'agents', label: 'Start subagents', summary: '', usage: '', needs: null }];
  expect(impliedTools(catalog, { repositories_enabled: true, documents_enabled: false, ssh_enabled: false })).toEqual(['read_file']);
});
