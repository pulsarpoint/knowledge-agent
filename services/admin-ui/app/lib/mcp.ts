import { request } from './api';
export interface MCPServer {
  id: string; name: string; description: string; url: string; enabled: boolean; has_token: boolean; revision: number;
  tools: { name: string; description: string }[]; last_tested_at: string | null; last_test_error: string | null;
}
export interface MCPInput { revision?: number; name: string; description: string; url: string; enabled: boolean; token?: string | null }
export interface MCPSelection { revision: number; mcp_server_ids: string[] }
const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
export const getMCPServers = () => request<{ servers: MCPServer[] }>('/api/mcp/servers');
export const saveMCPServer = (id: string | null, value: MCPInput) => request<MCPServer>(`/api/mcp/servers${id ? `/${id}` : ''}`, json(id ? 'PUT' : 'POST', value));
export const testMCPServer = (server: MCPServer) => request<{ ok: boolean; message: string; server: MCPServer }>(`/api/mcp/servers/${server.id}/test`, json('POST', { revision: server.revision }));
export const deleteMCPServer = (server: MCPServer) => request(`/api/mcp/servers/${server.id}`, json('DELETE', { revision: server.revision, confirmed: true }));
export const getCodeMCP = (agentId = "code") => request<MCPSelection>(`/api/mcp/code?agent_id=${encodeURIComponent(agentId)}`);
export const saveCodeMCP = (value: MCPSelection, agentId = "code") => request<MCPSelection>(`/api/mcp/code?agent_id=${encodeURIComponent(agentId)}`, json('PUT', value));
