import { useEffect, useState } from 'react';
import { ApiError } from '../lib/api';
import { getMCPServers, getCodeMCP, saveCodeMCP, type MCPSelection as Selection, type MCPServer } from '../lib/mcp';
import MCPSelection from './MCPSelection';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '~/components/ui/card';
import { Button } from '~/components/ui/button';
import { Alert, AlertDescription } from '~/components/ui/alert';

export default function CodeMCPSettings({ agentId = "code" }: { agentId?: string }) {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  async function refresh() {
    const [catalog, current] = await Promise.all([getMCPServers(), getCodeMCP(agentId)]);
    setServers(catalog.servers); setSelection(current); setDirty(false); setError(null);
  }
  async function act(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); } catch (e) { setError(e instanceof ApiError ? e.message : 'Could not load or save MCP settings.'); }
    finally { setBusy(false); }
  }
  useEffect(() => { void act(refresh); }, [agentId]);
  return <Card><CardHeader><CardTitle>Additional tools</CardTitle><CardDescription>MCP connections for code investigations, including code searches requested by the Search agent.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-4">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      {selection && <MCPSelection servers={servers} selected={selection.mcp_server_ids} disabled={busy} onChange={ids => { setSelection({ ...selection, mcp_server_ids: ids }); setDirty(true); setNotice(null); }} />}
    </CardContent><CardFooter className="flex gap-3"><Button disabled={busy || !dirty || !selection} onClick={() => void act(async () => { if (selection) { setSelection(await saveCodeMCP(selection, agentId)); setDirty(false); setNotice('Code agent MCP selection saved.'); } })}>Save MCP selection</Button>
      <Button variant="outline" disabled={busy} onClick={() => void act(refresh)}>{dirty ? 'Discard changes' : 'Refresh MCP servers'}</Button></CardFooter>
  </Card>;
}
