import { useEffect, useMemo, type ReactNode } from "react";
import { Background, Controls, ReactFlow, useNodesState, type Node } from "@xyflow/react";
import { LayoutGrid } from "lucide-react";
import { useSearchParams } from "react-router";
import type { ServiceAsset } from "../lib/services";
import { serviceDependencyD2, serviceDependencyGraph } from "../lib/serviceGraph";
import ServiceTalaGraph from "./ServiceTalaGraph";
import { serviceRoles } from "../lib/serviceClassification";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";

const fitViewOptions = { padding: 0.2, maxZoom: 1 };

export default function ServiceDependencyGraph({ services, onSelect }: { services: ServiceAsset[]; onSelect: (id: string) => void }) {
  const [params, setParams] = useSearchParams();
  const layout = params.get("layout") === "dagre" ? "dagre" : "tala";
  const d2Source = useMemo(() => serviceDependencyD2(services), [services]);
  const { nodes: layoutNodes, edges, relationships } = useMemo(() => serviceDependencyGraph(services), [services]);
  const initialNodes = useMemo(() => layoutNodes.map((node) => ({
    ...node,
    data: { label: <div className="flex min-w-0 flex-col items-center gap-1"><Button variant="link" className="nodrag h-auto max-w-full p-0" title={node.data.label}
      aria-label={`Open ${node.data.label} details`} onClick={() => onSelect(node.id)}>
      <span className="line-clamp-2 whitespace-normal break-words">{node.data.label}</span>
    </Button><span className="text-xs">{node.data.roleLabel}</span></div> },
  })), [layoutNodes, onSelect]);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<{ label: ReactNode }>>(initialNodes);
  useEffect(() => { setNodes(initialNodes); }, [initialNodes, setNodes]);
  return <section className="flex min-w-0 flex-col gap-4" aria-label="Service dependency graph">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="text-lg font-semibold">Dependency graph</h2>
        <p className="text-sm text-muted-foreground">A → B means A depends on B.</p>
      </div>
      <p className="text-sm text-muted-foreground">{services.length} {services.length === 1 ? "service" : "services"} · {relationships.length} {relationships.length === 1 ? "relationship" : "relationships"}</p>
    </div>
    <ul aria-label="Service role colors" className="flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted-foreground">
      {Object.entries(serviceRoles).filter(([role]) => services.some(service => service.service_role === role)).map(([name, role]) =>
        <li key={name} className="flex items-center gap-2"><span aria-hidden="true" className="size-3 rounded-sm border" style={{ backgroundColor: role.fill, borderColor: role.stroke }} />{role.label}</li>)}
    </ul>
    {relationships.length === 0 && <Alert><AlertDescription>No dependencies between these services. Edit a service and select “Depends on” or “Used by” to connect it.</AlertDescription></Alert>}
    <Tabs value={layout} onValueChange={value => setParams(previous => {
      const next = new URLSearchParams(previous);
      if (value === "dagre") next.set("layout", "dagre"); else next.delete("layout");
      return next;
    })}>
      <TabsList aria-label="Graph layout"><TabsTrigger value="tala">TALA</TabsTrigger><TabsTrigger value="dagre">Interactive (Dagre)</TabsTrigger></TabsList>
      <TabsContent value="tala" className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">Scroll to zoom and drag to pan. Open service details from the table below.</p>
        <ServiceTalaGraph source={d2Source} />
      </TabsContent>
      <TabsContent value="dagre" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">Drag services to rearrange them; click a name to open details.</p>
          <Button variant="outline" size="sm" onClick={() => setNodes(initialNodes)}><LayoutGrid data-icon="inline-start" />Auto layout</Button>
        </div>
        <div className="service-dependency-flow h-[520px] min-w-0 overflow-hidden rounded-lg border" aria-label="Dependency diagram">
          <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange}
            nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null}
            fitView fitViewOptions={fitViewOptions} minZoom={0.1} maxZoom={2}
            defaultMarkerColor="var(--muted-foreground)" colorMode="light"
            ariaLabelConfig={{ 'node.a11yDescription.default': 'Press Enter or Space to select a service, then use arrow keys to move it. Tab to its name to open details.', 'edge.a11yDescription.default': 'This arrow points to the service dependency.' }}>
            <Background gap={20} size={1} />
            <Controls showInteractive={false} fitViewOptions={fitViewOptions} />
          </ReactFlow>
        </div>
      </TabsContent>
    </Tabs>
    {relationships.length > 0 && <section className="flex flex-col gap-3" aria-label="Relationship details">
      <h3 className="font-semibold">Relationship details</h3>
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Service</TableHead><TableHead>Depends on</TableHead><TableHead>Why</TableHead></TableRow></TableHeader>
        <TableBody>{relationships.map(({ service, dependency, description }) => <TableRow key={`${service.id}:${dependency.id}`}>
          <TableCell><Button variant="link" className="h-auto whitespace-normal p-0 text-left" onClick={() => onSelect(service.id)}>{service.name}</Button></TableCell>
          <TableCell><Button variant="link" className="h-auto whitespace-normal p-0 text-left" onClick={() => onSelect(dependency.id)}>{dependency.name}</Button></TableCell>
          <TableCell className="max-w-lg whitespace-pre-wrap break-words">{description || "No description"}</TableCell>
        </TableRow>)}</TableBody>
      </Table></div>
    </section>}
  </section>;
}
