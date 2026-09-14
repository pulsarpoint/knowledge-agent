import { describe, expect, test } from "vitest";
import { parseSse } from "../sse";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) { chunks.forEach((ch) => c.enqueue(enc.encode(ch))); c.close(); },
  });
}

async function collect(s: ReadableStream<Uint8Array>) {
  const out = [];
  for await (const ev of parseSse(s)) out.push(ev);
  return out;
}

test("parses events split across chunks", async () => {
  const events = await collect(streamOf(
    'event: token\ndata: {"te', 'xt": "Hel"}\n\nevent: token\ndata: {"text": "lo"}\n\n',
  ));
  expect(events).toEqual([
    { event: "token", data: { text: "Hel" } },
    { event: "token", data: { text: "lo" } },
  ]);
});

test("ignores blank keepalives and unparseable data", async () => {
  const events = await collect(streamOf("\n\nevent: done\ndata: not-json\n\nevent: done\ndata: {\"text\":\"x\",\"not_covered\":false}\n\n"));
  expect(events).toEqual([{ event: "done", data: { text: "x", not_covered: false } }]);
});
