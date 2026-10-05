import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import ChatPanel from "@knowledge-agent/chat-ui";
import * as client from "../lib/agents";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";

export default function SearchAgentView({ modelRevision = 0, agentId = "search" }: { modelRevision?: number; agentId?: string }) {
  const scopedClient = useMemo(() => client.forAgent(agentId), [agentId]);
  const [status, setStatus] = useState<client.AgentStatus>();
  const [error, setError] = useState<string>();
  const [session, setSession] = useState<string>();
  const [conversation, setConversation] = useState(0);
  useEffect(() => {
    let active = true;
    client.getAgentStatus(agentId).then((value) => { if (active) { setStatus(value); setError(undefined); } })
      .catch((error: Error) => { if (active) setError(error.message); });
    return () => { active = false; };
  }, [modelRevision, agentId]);
  if (error) return <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>;
  if (!status) return <Skeleton className="h-64 w-full" />;
  return <section className="flex flex-col gap-6" aria-label="Search agent">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="flex flex-col gap-2"><h2 className="text-lg font-semibold">Search agent</h2>
        <p className="text-sm text-muted-foreground">Configure the context and submit a test request. Agent execution is not implemented yet.</p>
        <div className="flex flex-wrap gap-2"><Badge variant="secondary">RAG</Badge><Badge variant="secondary">Source code</Badge>{status.model && <Badge variant="outline">{status.model}</Badge>}</div>
      </div>
      <Button variant="outline" onClick={() => { setSession(undefined); setConversation((n) => n + 1); }}>New conversation</Button>
    </div>
    <div className="flex flex-wrap gap-4 text-sm"><Link className="underline" to="/rag/collections">Manage RAG collections</Link><Link className="underline" to="/github-projects">Manage repositories</Link></div>
    <ChatPanel key={conversation} app="admin-search" contextEnabled askEnabled={status.ask_enabled} askReason={status.ask_reason}
      sources={status.sources} sessionKey={session} onSession={setSession} client={scopedClient}
      documentHref={(source) => `/rag/files?path=${encodeURIComponent(source.path)}`} />
  </section>;
}
