import { formatAttributes, linkKindsFrom, mapQuery, parseAttributes, readMapState, targetKinds, viewUrl } from "../inventoryMap";

test("reads and writes the page state with defaults", () => {
  expect(readMapState(new URLSearchParams(""))).toEqual({ map: "infrastructure", root: null, depth: 1, view: "diagram", layout: "tala" });
  expect(readMapState(new URLSearchParams("map=services&depth=9&view=bogus"))).toEqual({ map: "services", root: null, depth: 1, view: "diagram", layout: "tala" });
  expect(mapQuery({ map: "services", root: "service:1", depth: 2, view: "d2", layout: "elk" }).toString())
    .toBe("map=services&root=service%3A1&depth=2&view=d2&layout=elk");
  expect(viewUrl("infrastructure", "server:top:1", 1, "svg", "dagre")).toBe("/api/map/view/infrastructure?root=server%3Atop%3A1&depth=1&format=svg&layout=dagre");
});

test("link kinds follow the entry kinds the API allows", () => {
  expect(linkKindsFrom("service")).toEqual(["connects_to", "depends_on", "built_from"]);
  expect(linkKindsFrom("server")).toEqual(["connects_to"]);
  expect(targetKinds("depends_on")).toEqual(["service"]);
  expect(targetKinds("built_from")).toEqual(["code_project"]);
  expect(targetKinds("connects_to")).toBeUndefined();
});

test("attributes are edited as name: value lines", () => {
  expect(parseAttributes("")).toEqual({ value: {} });
  expect(parseAttributes("port: 5432\n\nprotocol:  https:443 ")).toEqual({ value: { port: "5432", protocol: "https:443" } });
  expect(parseAttributes("no colon")).toEqual({ problem: "Write each attribute as name: value." });
  expect(parseAttributes(": x")).toEqual({ problem: "Write each attribute as name: value." });
  expect(parseAttributes("a: 1\na: 2")).toEqual({ problem: 'The attribute "a" appears twice.' });
  expect(parseAttributes(Array.from({ length: 21 }, (_, i) => `k${i}: v`).join("\n"))).toEqual({ problem: "Use at most 20 attributes." });
  expect(parseAttributes(`${"k".repeat(65)}: v`)).toEqual({ problem: "Attribute names are 1–64 characters." });
  expect(parseAttributes(`k: ${"v".repeat(501)}`)).toEqual({ problem: "Attribute values are at most 500 characters." });
  expect(formatAttributes({ port: "5432", protocol: "https" })).toBe("port: 5432\nprotocol: https");
});
