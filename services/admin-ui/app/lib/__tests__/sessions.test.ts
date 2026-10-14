import { afterEach, expect, test, vi } from 'vitest';
import { getSessionOptions, getSessions, reviewSession, savedIn, sessionPath, startSession } from '~/features/sessions/api';
import type { Turn } from '~/features/sessions/turns';

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
  await getSessionOptions({ documentId: 'd1' });
  await getSessionOptions({});
  await getSessionOptions({ agentId: 'ops' });
  await getSessions({ agentId: 'ops' });
  await reviewSession('s 1', 'Shorter', 2);
  await startSession({ started_from: { document_id: 'd1' }, message: 'Hi', model_id: 'm', prompt_id: null, system_prompt: 'S',
    context: '', resources: { server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [], documents: [] },
    tools: [], expert_ids: [], subagents: false });
  expect(calls).toEqual(['GET /api/sessions?document_id=d%201', 'GET /api/sessions?purpose=documents&offset=50',
    'GET /api/sessions/options?document_id=d1', 'GET /api/sessions/options', 'GET /api/sessions/options?agent_id=ops', 'GET /api/sessions?agent_id=ops',
    'POST /api/sessions/s%201/review', 'POST /api/sessions']);
  expect(sessionPath('a/b')).toBe('/sessions/a%2Fb');
});

test('a turn saved its draft with the new or the old save tools', () => {
  const turn = (name: string) => ({ work: [{ kind: 'tool', name, isError: false, preview: 'Saved draft (3 lines).' }] }) as unknown as Turn;
  expect(['save_document', 'edit_document', 'save_doc', 'edit_doc'].map(name => savedIn(turn(name)))).toEqual([true, true, true, true]);
  expect(savedIn(turn('get_document'))).toBe(false);
});

test('a review is posted with its comment and turn', async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => { bodies.push(init?.body); return new Response('{}', { status: 200 }); }));
  await reviewSession('s 1', 'Shorter', 2);
  expect(JSON.parse(String(bodies[0]))).toEqual({ comment: 'Shorter', turn: 2 });
});
