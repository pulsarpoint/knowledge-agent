import { afterEach, expect, test, vi } from 'vitest';
import { getSessionOptions, getSessions, savedIn, sessionPath, startSession } from '../sessions';
import type { Turn } from '../turns';

afterEach(() => vi.unstubAllGlobals());
function api() {
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${url}`);
    return new Response(JSON.stringify({ sessions: [] }), { status: 200 });
  }));
  return calls;
}

test('the calls use /api/sessions', async () => {
  const calls = api();
  await getSessions({ documentId: 'd 1' });
  await getSessions({ purpose: 'documents', offset: 50 });
  await getSessionOptions('d1');
  await getSessionOptions(null);
  await startSession({ started_from: { document_id: 'd1' }, message: 'Hi', model_id: 'm', prompt_id: null, system_prompt: 'S',
    context: '', resources: { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [], documents: [] },
    tools: [], expert_ids: [], subagents: false });
  expect(calls).toEqual(['GET /api/sessions?document_id=d%201', 'GET /api/sessions?purpose=documents&offset=50',
    'GET /api/sessions/options?document_id=d1', 'GET /api/sessions/options', 'POST /api/sessions']);
  expect(sessionPath('a/b')).toBe('/sessions/a%2Fb');
});

test('a turn saved its draft with the new or the old save tools', () => {
  const turn = (name: string) => ({ work: [{ kind: 'tool', name, isError: false, preview: 'Saved draft (3 lines).' }] }) as unknown as Turn;
  expect(['save_document', 'edit_document', 'save_doc', 'edit_doc'].map(name => savedIn(turn(name)))).toEqual([true, true, true, true]);
  expect(savedIn(turn('get_document'))).toBe(false);
});
