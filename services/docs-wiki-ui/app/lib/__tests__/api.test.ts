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

test.each(['chat', 'escalate'])("%s reads the plain TODO response without session events", async kind => {
  const fetchMock = vi.fn(async () => new Response('TODO', { headers: { 'Content-Type': 'text/plain' } }));
  vi.stubGlobal('fetch', fetchMock);
  const events = [];
  const result = kind === 'chat' ? streamChat('shop', 'hello?') : escalateStream('shop', 'saved', 'hello?');
  for await (const event of result) events.push(event);
  expect(events).toEqual([{ type: 'todo', text: 'TODO' }]);
  expect(fetchMock).toHaveBeenCalledWith(kind === 'chat' ? '/api/shop/chat' : '/api/shop/chat/saved/escalate', expect.objectContaining({ method: 'POST', body: JSON.stringify({ question: 'hello?' }) }));
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
