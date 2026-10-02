import { afterEach, expect, test, vi } from "vitest";
import { ApiError, escalateStream, getDoc, streamChat } from "../api";

afterEach(() => vi.unstubAllGlobals());

test("getDoc hits the right URL and returns json", async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ path: "a.md", title: "A", markdown: "# A", front_matter: {}, freshness: "unknown", headings: [] })));
  vi.stubGlobal("fetch", fetchMock);
  const doc = await getDoc("shop", "a.md");
  expect(fetchMock).toHaveBeenCalledWith("/api/shop/doc?path=a.md");
  expect(doc.title).toBe("A");
});

test("non-ok responses raise ApiError with the server message", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify({ error: { message: "unknown app" } }), { status: 404 })));
  await expect(getDoc("ghost", "a.md")).rejects.toMatchObject({ status: 404, message: "unknown app" });
  expect(new ApiError("x", 400)).toBeInstanceOf(Error);
});

test("escalate reads the plain TODO response without session events", async () => {
  const fetchMock = vi.fn(async () => new Response('TODO', { headers: { 'Content-Type': 'text/plain' } }));
  vi.stubGlobal('fetch', fetchMock);
  const events = [];
  for await (const event of escalateStream('shop', 'saved', 'hello?')) events.push(event);
  expect(events).toEqual([{ type: 'todo', text: 'TODO' }]);
  expect(fetchMock).toHaveBeenCalledWith('/api/shop/chat/saved/escalate', expect.objectContaining({ method: 'POST', body: JSON.stringify({ question: 'hello?' }) }));
});

function sseResponse(text: string, chunks = 1) {
  const bytes = new TextEncoder().encode(text);
  const size = Math.ceil(bytes.length / chunks);
  return new Response(new ReadableStream<Uint8Array>({ start(c) {
    for (let i = 0; i < bytes.length; i += size) c.enqueue(bytes.slice(i, i + size));
    c.close();
  } }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

test('streamChat parses reader events across chunk boundaries', async () => {
  const text = 'event: session\ndata: {"key":"s1"}\n\n' +
    'event: tool\ndata: {"name":"search_documents","argument":"{\\"query\\":\\"retries\\"}"}\n\n' +
    'event: sources\ndata: {"sources":[{"n":1,"path":"p/r.md","heading_path":"p","anchor":null}]}\n\n' +
    'event: done\ndata: {"text":"Three [1].","not_covered":false,"escalation_offered":false,"sources_used":["docs"]}\n\n';
  vi.stubGlobal('fetch', vi.fn(async () => sseResponse(text, 7)));
  const events = [];
  for await (const event of streamChat('shop', 'retries?')) events.push(event);
  expect(events.map(e => e.type)).toEqual(['session', 'tool', 'sources', 'done']);
  expect(events[0]).toEqual({ type: 'session', key: 's1' });
  expect(events[3]).toMatchObject({ type: 'done', text: 'Three [1].', not_covered: false });
});

test('streamChat reports an error when the stream ends without an answer', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => sseResponse('event: session\ndata: {"key":"s1"}\n\n')));
  const events = [];
  for await (const event of streamChat('shop', 'q')) events.push(event);
  expect(events.at(-1)).toEqual({ type: 'error', message: 'Chat is temporarily unavailable.' });
});

test("streamChat includes sources in the body when passed", async () => {
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new TextEncoder().encode('event: done\ndata: {"text": "hi", "not_covered": false}\n\n'));
      c.close();
    },
  });
  const fetchMock = vi.fn(async () => new Response(body, { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  const events = [];
  for await (const ev of streamChat("shop", "hello?", undefined, { docs: true, codex: true })) {
    events.push(ev);
  }
  expect(fetchMock).toHaveBeenCalledWith("/api/shop/chat", expect.objectContaining({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: "hello?", sources: { docs: true, codex: true } }),
  }));
});

test("reader chat sends advanced context with the selected sources", async () => {
  const fetchMock = vi.fn(async () => new Response('event: done\ndata: {"text":"ok","not_covered":false}\n\n'));
  vi.stubGlobal("fetch", fetchMock);
  const context = { folders: ['payments', 'support/incidents'], include_subfolders: true, repositories: null };
  for await (const _event of streamChat('docs', 'Explain', undefined, { docs: true, codex: false }, context)) { /* drain */ }
  expect(fetchMock).toHaveBeenCalledWith('/api/docs/chat', expect.objectContaining({
    body: JSON.stringify({ question: 'Explain', sources: { docs: true, codex: false }, context }),
  }));
});

test("streamChat surfaces pre-stream API errors as an error event", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify({ error: { message: "Ask is not configured" } }), { status: 400 })));
  const events = [];
  for await (const ev of streamChat("shop", "hello?")) events.push(ev);
  expect(events).toEqual([{ type: "error", message: "Ask is not configured" }]);
});

test("escalateStream surfaces pre-stream API errors as an error event", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(JSON.stringify({ error: { message: "code analysis is not configured" } }), { status: 400 })));
  const events = [];
  for await (const ev of escalateStream("shop", "k1", "hello?")) events.push(ev);
  expect(events).toEqual([{ type: "error", message: "code analysis is not configured" }]);
});
