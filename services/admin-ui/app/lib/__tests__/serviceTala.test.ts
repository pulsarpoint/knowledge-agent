import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { serviceDependencyD2 } from "~/features/servers/serviceGraph";

test("real TALA compilation preserves labels, cycles, isolated services and dependency direction", () => {
  const unusualName = 'API "prod" ${untrusted} \\ <img src=x onerror=alert(1)>\n# @import';
  const services = [
    { id: "api", name: unusualName, service_role: "application" as const, dependencies: [{ service_id: "db", name: "DB", description: "Stores data" }] },
    { id: "db", name: "DB", service_role: "database" as const, dependencies: [{ service_id: "queue", name: "Queue", description: "" }] },
    { id: "queue", name: "Queue", service_role: "message_broker" as const, dependencies: [{ service_id: "api", name: unusualName, description: "" }] },
    { id: "isolated", name: "Unconnected", service_role: "unclassified" as const, dependencies: [] },
  ];
  // Run the real WASM worker in Node, independent of jsdom's browser globals.
  const packagePath = createRequire(import.meta.url).resolve("@d2lang/d2");
  const result = JSON.parse(execFileSync(process.execPath, ["-e", `
    const { D2 } = require(process.argv[1]);
    const fs = require('node:fs');
    (async () => {
      const engine = new D2();
      try {
        const sources = JSON.parse(fs.readFileSync(0, 'utf8'));
        const diagrams = [];
        for (const source of sources) {
          const result = await engine.compile(source, { layout: 'tala', pad: 24 });
          const svg = await engine.render(result.diagram, result.renderOptions);
          diagrams.push({ shapes: result.diagram.shapes, connections: result.diagram.connections, svg });
        }
        process.stdout.write(JSON.stringify(diagrams));
      } finally { await engine.dispose(); }
    })().catch(error => { console.error(error); process.exitCode = 1; });
  `, packagePath], {
    input: JSON.stringify([serviceDependencyD2(services), serviceDependencyD2([services[0]])]),
    encoding: "utf8", timeout: 20_000,
  }));
  expect(result[0].shapes.map((shape: { label: string }) => shape.label).sort()).toEqual([
    `${unusualName}\nApplication`, 'DB\nDatabase', 'Queue\nMessage broker', 'Unconnected\nUnclassified',
  ].sort());
  expect(result[0].connections.map((edge: { src: string; dst: string }) => [edge.src, edge.dst])).toEqual([
    ["service0", "service1"], ["service1", "service2"], ["service2", "service0"],
  ]);
  const svg = new DOMParser().parseFromString(result[0].svg, "image/svg+xml");
  expect(svg.querySelector("parsererror, script, img, image, foreignObject")).toBeNull();
  expect(svg.documentElement.getAttribute("data-d2-version")).toBe("v0.9.0");
  for (const color of ["#dbeafe", "#ede9fe", "#fef3c7", "#f1f5f9"]) {
    expect(svg.querySelector(`[fill="${color}"]`)).not.toBeNull();
  }
  // A stale link to a deleted service must not create a phantom node.
  expect(result[1].shapes).toHaveLength(1);
  expect(result[1].connections).toEqual([]);
}, 25_000);
