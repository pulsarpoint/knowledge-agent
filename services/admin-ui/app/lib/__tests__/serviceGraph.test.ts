import { MarkerType } from '@xyflow/react';
import { serviceDependencyGraph } from '../serviceGraph';

const services = [
  { id: 'api', name: 'Payments', service_role: 'application' as const, dependencies: [{ service_id: 'db', name: 'PostgreSQL', description: 'Stores payments' }] },
  { id: 'db', name: 'PostgreSQL', service_role: 'database' as const, dependencies: [] },
  { id: 'worker', name: 'Worker', service_role: 'worker' as const, dependencies: [] },
];

test('lays dependencies out left to right and retains isolated services', () => {
  const { nodes, edges, relationships } = serviceDependencyGraph(services);
  expect(nodes.map(node => node.id)).toEqual(['api', 'db', 'worker']);
  expect(nodes[0].position.x + nodes[0].width!).toBeLessThan(nodes[1].position.x);
  expect(edges.map(edge => [edge.source, edge.target])).toEqual([['api', 'db']]);
  expect(edges[0].markerEnd).toEqual({ type: MarkerType.ArrowClosed });
  expect(relationships[0].description).toBe('Stores payments');
  expect(edges[0].ariaLabel).toBe('Payments depends on PostgreSQL: Stores payments');
  expect(nodes.every(node => Number.isFinite(node.position.x) && Number.isFinite(node.position.y))).toBe(true);
});

test('mutual dependencies remain two directed edges with distinct identities', () => {
  const { nodes, edges } = serviceDependencyGraph([services[0], { ...services[1], dependencies: [{ service_id: 'api', name: 'Payments', description: 'Callbacks' }] }]);
  expect(edges.map(edge => [edge.source, edge.target])).toEqual([['api', 'db'], ['db', 'api']]);
  expect(new Set(edges.map(edge => edge.id)).size).toBe(2);
  expect(nodes[0].position).not.toEqual(nodes[1].position);
});

test('longer cycles produce finite positions without overlapping services', () => {
  const { nodes, edges } = serviceDependencyGraph([
    services[0],
    { ...services[1], dependencies: [{ service_id: 'worker', name: 'Worker', description: '' }] },
    { ...services[2], dependencies: [{ service_id: 'api', name: 'Payments', description: '' }] },
  ]);
  expect(edges).toHaveLength(3);
  for (const node of nodes) {
    expect(Number.isFinite(node.position.x) && Number.isFinite(node.position.y)).toBe(true);
    for (const other of nodes.filter(other => other.id !== node.id)) {
      expect(node.position.x + node.width! <= other.position.x || other.position.x + other.width! <= node.position.x ||
        node.position.y + node.height! <= other.position.y || other.position.y + other.height! <= node.position.y).toBe(true);
    }
  }
});

test('names remain plain labels, independent of diagram syntax', () => {
  const name = 'API "prod" [v2] & <img src=x onerror=alert(1)> --> other';
  const { nodes, edges } = serviceDependencyGraph([{ ...services[0], name }, services[1]]);
  expect(nodes[0].data.label).toBe(name);
  expect(nodes.map(node => node.id)).toEqual(['api', 'db']);
  expect(edges).toHaveLength(1);
});

test('removed nodes and their edges do not survive subsequent layouts', () => {
  serviceDependencyGraph(services);
  const { nodes, edges, relationships } = serviceDependencyGraph([services[0]]);
  expect(nodes.map(node => node.id)).toEqual(['api']);
  expect(relationships).toEqual([]);
  expect(edges).toEqual([]);
  expect(serviceDependencyGraph([])).toEqual({ nodes: [], edges: [], relationships: [] });
});
