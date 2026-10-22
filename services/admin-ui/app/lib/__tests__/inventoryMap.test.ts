import { environmentMapPath, mapQuery, readMapState, viewUrl } from "~/features/servers/inventoryMap";

test("reads and writes the page state with defaults", () => {
  expect(readMapState(new URLSearchParams(""))).toEqual({
    map: "infrastructure",
    root: null,
    depth: 1,
    view: "diagram",
    layout: "tala",
  });
  expect(readMapState(new URLSearchParams("map=services&depth=9&view=bogus"))).toEqual({
    map: "services",
    root: null,
    depth: 1,
    view: "diagram",
    layout: "tala",
  });
  expect(mapQuery({ map: "services", root: "service:1", depth: 2, view: "d2", layout: "elk" }).toString()).toBe(
    "map=services&root=service%3A1&depth=2&view=d2&layout=elk",
  );
  expect(viewUrl("infrastructure", "server:top:1", 1, "svg", "dagre")).toBe(
    "/api/map/view/infrastructure?root=server%3Atop%3A1&depth=1&format=svg&layout=dagre",
  );
});

test("an environment's map is the infrastructure map rooted at it, two levels deep", () => {
  expect(environmentMapPath("e1")).toBe("/system-map?map=infrastructure&root=environment%3Ae1&depth=2");
});
