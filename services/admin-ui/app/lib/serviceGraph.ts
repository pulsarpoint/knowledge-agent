import dagre from "@dagrejs/dagre";
import { MarkerType, Position, type Edge, type Node } from "@xyflow/react";
import type { ServiceAsset } from "./services";
import type { CSSProperties } from "react";
import { serviceRoles } from "./serviceClassification";

type GraphService = Pick<ServiceAsset, "id" | "name" | "dependencies" | "service_role">;

export function serviceDependencyD2(services: GraphService[]): string {
  const ids = new Map(services.map((service, index) => [service.id, `service${index}`]));
  const lines = Object.entries(serviceRoles).map(([name, role]) =>
    `classes.service_${name}: {style: {fill: "${role.fill}"; stroke: "${role.stroke}"; font-color: "${role.text}"; stroke-width: 2; border-radius: 8}; width: 220; height: 72}`);
  for (const service of services) {
    // Quoted labels must also escape D2 variable substitutions. IDs never use names.
    const label = JSON.stringify(`${service.name}\n${serviceRoles[service.service_role].label}`).replaceAll("$", "\\$");
    lines.push(`${ids.get(service.id)}: ${label} {class: service_${service.service_role}}`);
    for (const dependency of service.dependencies) {
      if (ids.has(dependency.service_id)) lines.push(`${ids.get(service.id)} -> ${ids.get(dependency.service_id)}`);
    }
  }
  return lines.join("\n");
}

export function serviceDependencyGraph(services: GraphService[]) {
  const byId = new Map(services.map((service) => [service.id, service]));
  const relationships = services.flatMap((service) => service.dependencies.flatMap((link) => {
    const dependency = byId.get(link.service_id);
    // A refresh following deletion may briefly contain a link to a removed service.
    return dependency ? [{ service, dependency, description: link.description }] : [];
  }));
  // Use a fresh graph so removed services do not survive a later layout.
  const layout = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  layout.setGraph({ rankdir: "LR", ranksep: 100, nodesep: 40, marginx: 24, marginy: 24 });
  const width = 240;
  const height = 80;
  for (const service of services) layout.setNode(service.id, { width, height });
  // "Used by" is the inverse of this list, not another set of edges.
  for (const { service, dependency } of relationships) layout.setEdge(service.id, dependency.id);
  dagre.layout(layout);

  const nodes: Node<{ label: string; roleLabel: string }>[] = services.map((service) => {
    const position = layout.node(service.id);
    const role = serviceRoles[service.service_role];
    return {
      id: service.id, data: { label: service.name, roleLabel: role.label }, ariaLabel: `Service: ${service.name}`,
      position: { x: position.x - width / 2, y: position.y - height / 2 },
      width, height, style: { width, height, backgroundColor: role.fill, borderColor: role.stroke, color: role.text,
        "--service-node-foreground": role.text, "--xy-handle-background-color": role.stroke,
        "--xy-node-boxshadow-selected": `0 0 0 3px ${role.stroke}` } as CSSProperties,
      sourcePosition: Position.Right, targetPosition: Position.Left,
      deletable: false,
    };
  });
  const edges: Edge[] = relationships.map(({ service, dependency, description }) => ({
    id: `${service.id}:${dependency.id}`, source: service.id, target: dependency.id,
    type: "smoothstep", markerEnd: { type: MarkerType.ArrowClosed },
    ariaLabel: `${service.name} depends on ${dependency.name}${description ? `: ${description}` : ""}`,
    data: { description }, deletable: false, reconnectable: false, selectable: false,
  }));
  return { nodes, edges, relationships };
}
