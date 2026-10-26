import { useState } from "react";
import { Link, Navigate, useLocation, useSearchParams } from "react-router";
import LeaveGuard from "~/components/LeaveGuard";
import AgentChatView from "./AgentChatView";
import { useAgentPage } from "./AgentLayout";
import AgentProfileSettings from "./AgentProfileSettings";
import AgentPromptCard from "./AgentPromptCard";
import AgentResources from "./AgentResources";

/** `/agents/:agentId`: opens General; an old link (`?view=general|resources|chat|test`) opens its sub-page (test: Chat),
 * keeping the rest of the query. */
export function AgentIndex() {
  const [current] = useSearchParams();
  const { hash } = useLocation();
  const params = new URLSearchParams(current);
  const view = params.get("view");
  params.delete("view");
  const page = view === "chat" || view === "test" ? "chat" : view === "resources" ? "resources" : "general";
  const query = params.toString();
  return <Navigate replace to={`${page}${query ? `?${query}` : ""}${hash}`} />;
}

/** The agent's details, model and instructions, and its prompt. */
export function AgentGeneralPage() {
  const { profile, setProfile, reloaded } = useAgentPage();
  const [detailsDirty, setDetailsDirty] = useState(false);
  const [promptDirty, setPromptDirty] = useState(false);
  return (
    <div className="flex flex-col gap-6">
      {profile.original && (
        <p className="text-sm text-muted-foreground">
          Clone of{" "}
          <Link className="underline underline-offset-4" to={`/agents/${encodeURIComponent(profile.original.id)}`}>
            {profile.original.name}
          </Link>
        </p>
      )}
      <AgentProfileSettings key={reloaded} profile={profile} onSaved={setProfile} onDirtyChange={setDetailsDirty} />
      <AgentPromptCard profile={profile} onSaved={setProfile} onDirtyChange={setPromptDirty} />
      <LeaveGuard dirty={detailsDirty || promptDirty} />
    </div>
  );
}

/** What the agent may use. */
export function AgentResourcesPage() {
  const { profile, setProfile, reloaded } = useAgentPage();
  const [dirty, setDirty] = useState(false);
  return (
    <>
      <AgentResources key={reloaded} profile={profile} onSaved={setProfile} onDirtyChange={setDirty} />
      <LeaveGuard dirty={dirty} />
    </>
  );
}

/** A chat with the agent. */
export function AgentChatPage() {
  const { profile, reload } = useAgentPage();
  return (
    <AgentChatView
      agentId={profile.id}
      agentName={profile.name}
      available={profile.enabled}
      revision={profile.revision}
      onAgentChanged={reload}
    />
  );
}
