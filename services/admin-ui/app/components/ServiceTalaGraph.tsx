import { useEffect, useMemo, useState } from "react";
import { Background, Controls, ReactFlow, type Node, type NodeProps } from "@xyflow/react";
import type { D2 } from "@d2lang/d2";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Spinner } from "~/components/ui/spinner";

type Diagram = { label: string; url: string; width: number; height: number };
type DiagramNode = Node<Diagram, "diagram">;

function DiagramImage({ data }: NodeProps<DiagramNode>) {
  // Image embedding keeps generated SVG out of the page's DOM.
  return <img src={data.url} width={data.width} height={data.height} draggable={false} alt={data.label} />;
}

const nodeTypes = { diagram: DiagramImage };
const fitViewOptions = { padding: 0.15, maxZoom: 1 };

export default function ServiceTalaGraph({ source, label = "Service dependencies laid out by TALA. Arrows point to dependencies; relationship details are listed below." }: { source: string; label?: string }) {
  const [diagram, setDiagram] = useState<Diagram | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let engine: D2 | undefined;
    setDiagram(null);
    setError("");
    const timeout = window.setTimeout(() => {
      cancelled = true;
      setError("The diagram layout took too long. Try again or simplify the diagram.");
      void engine?.dispose();
    }, 60_000);
    async function render() {
      try {
        // The WASM engine is loaded only when the TALA view is opened.
        const { D2 } = await import("@d2lang/d2");
        if (cancelled) return;
        engine = new D2();
        const result = await engine.compile(source, { layout: "tala", pad: 24 });
        const svg = await engine.render(result.diagram, result.renderOptions);
        const root = new DOMParser().parseFromString(svg, "image/svg+xml").documentElement;
        const bounds = root.getAttribute("viewBox")?.trim().split(/\s+/).map(Number);
        if (root.localName !== "svg" || !bounds || bounds.length !== 4 || !bounds.every(Number.isFinite) || bounds[2] <= 0 || bounds[3] <= 0) {
          throw new Error("Invalid diagram dimensions");
        }
        if (!cancelled) setDiagram({ label, url: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`, width: bounds[2], height: bounds[3] });
      } catch {
        if (!cancelled) setError("Could not draw the diagram. Check the D2 source and try again.");
      } finally {
        window.clearTimeout(timeout);
        await engine?.dispose();
      }
    }
    void render();
    return () => { cancelled = true; window.clearTimeout(timeout); void engine?.dispose(); };
  }, [source, attempt, label]);

  const nodes = useMemo<DiagramNode[]>(() => diagram ? [{
    id: "tala-diagram", type: "diagram", position: { x: 0, y: 0 }, data: diagram,
    width: diagram.width, height: diagram.height, focusable: false,
  }] : [], [diagram]);

  if (error) return <Alert variant="destructive"><AlertDescription className="flex flex-wrap items-center justify-between gap-3">
    <span>{error}</span><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>Retry layout</Button>
  </AlertDescription></Alert>;
  if (!diagram) return <div role="status" className="flex h-[520px] items-center justify-center gap-2 rounded-lg border text-muted-foreground"><Spinner />Drawing dependencies…</div>;
  return <div className="service-dependency-flow h-[520px] min-w-0 overflow-hidden rounded-lg border" aria-label="TALA dependency diagram">
    <ReactFlow key={diagram.url} nodes={nodes} edges={[]} nodeTypes={nodeTypes}
      nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} deleteKeyCode={null}
      fitView fitViewOptions={fitViewOptions} minZoom={0.05} maxZoom={2} colorMode="light">
      <Background gap={20} size={1} />
      <Controls showInteractive={false} fitViewOptions={fitViewOptions} />
    </ReactFlow>
  </div>;
}
