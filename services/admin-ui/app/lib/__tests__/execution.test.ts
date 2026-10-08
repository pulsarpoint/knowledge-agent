import { afterEach, expect, test, vi } from 'vitest';
import { startOperationsRun, cancelOperationsRun, getAgentSessions } from '../operations';
import { deleteAgent, describeHistory, getAgentHistory } from '../profiles';
afterEach(() => vi.unstubAllGlobals());

test('execution clients preserve structured access errors', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ error: { message: 'Agent disabled' } }, { status: 409 })));
  await expect(startOperationsRun('Inspect', 'custom', new AbortController().signal, vi.fn())).rejects.toMatchObject({ status: 409, message: 'Agent disabled' });
});

test('direct runs send the selected agent and task once and consume live events', async () => {
  const fetchMock = vi.fn(async () => new Response('event: started\ndata: {"id":"run-1","status":"running"}\n\n: heartbeat\n\nevent: activity\ndata: {"id":"item-1","title":"Read README"}\n\nevent: completed\ndata: {"id":"run-1","status":"completed","answer":"Hello World"}\n\n'));
  vi.stubGlobal('fetch', fetchMock);
  const onEvent = vi.fn();
  const signal = new AbortController().signal;
  await startOperationsRun('Inspect README', 'agent/id', signal, onEvent);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledWith('/api/agents/agent%2Fid/runs', expect.objectContaining({ method:'POST', signal }));
  expect(JSON.parse((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as string)).toMatchObject({ task:'Inspect README', id:expect.any(String), is_test:true });
  expect(onEvent.mock.calls.map(([event]) => event.event)).toEqual(['started','activity','completed']);
});

test('incomplete test streams report an error without retrying the task', async () => {
  const fetchMock = vi.fn(async () => new Response('event: started\ndata: {"id":"run-1"}\n\n'));
  vi.stubGlobal('fetch', fetchMock);
  await expect(startOperationsRun('Inspect', 'custom', new AbortController().signal, vi.fn())).rejects.toThrow('not retried');
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test('cancellation targets the selected run and agent', async () => {
  const fetchMock = vi.fn(async () => Response.json({ id:'run-1' }));
  vi.stubGlobal('fetch', fetchMock);
  expect(await cancelOperationsRun('run-1','custom')).toEqual({ id:'run-1' });
  expect(fetchMock).toHaveBeenCalledWith('/api/agents/custom/runs/run-1/cancel', expect.objectContaining({ method:'POST' }));
});

test('the Test history lists only test sessions from the shared endpoint', async () => {
  const fetchMock = vi.fn(async () => Response.json({ sessions:[] }));
  vi.stubGlobal('fetch', fetchMock);
  await getAgentSessions('custom');
  expect(fetchMock).toHaveBeenCalledWith('/api/agents/custom/sessions?is_test=true');
});

test('agent deletion sends revision and explicit confirmation and accepts 204', async () => {
  const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
  vi.stubGlobal('fetch', fetchMock);
  await expect(deleteAgent('custom', 3)).resolves.toBeUndefined();
  expect(fetchMock).toHaveBeenCalledWith('/api/agents/custom', expect.objectContaining({ method: 'DELETE', body: JSON.stringify({ revision: 3, confirmed: true }) }));
});

test('an agent is deleted with its history only when asked, and its history reads as a short list', async () => {
  const fetchMock = vi.fn(async (url: string) => url.endsWith('/history')
    ? Response.json({ test_chats: 2, documentation_sessions: 1, run_records: 1 })
    : new Response(null, { status: 204 }));
  vi.stubGlobal('fetch', fetchMock);
  await deleteAgent('custom', 3, true);
  expect(fetchMock).toHaveBeenCalledWith('/api/agents/custom', expect.objectContaining({ method: 'DELETE', body: JSON.stringify({ revision: 3, confirmed: true, with_history: true }) }));
  const history = await getAgentHistory('custom');
  expect(fetchMock.mock.lastCall?.[0]).toBe('/api/agents/custom/history');
  expect(describeHistory(history)).toBe('2 test chats, 1 documentation session, 1 run record');
  expect(describeHistory({ test_chats: 0, documentation_sessions: 0, run_records: 0 })).toBe('');
});
