import { useState } from "react";
import { Link, Navigate, useBeforeUnload, useBlocker, useLocation, useSearchParams } from "react-router";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
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

/** Leaving the page (another section, back, any link) while it has unsaved changes asks first. */
function LeaveGuard({ dirty }: { dirty: boolean }) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname,
  );
  useBeforeUnload((event) => {
    if (dirty) event.preventDefault();
  });
  const stay = () => {
    if (blocker.state === "blocked") blocker.reset();
  };
  return (
    <AlertDialog open={blocker.state === "blocked"} onOpenChange={(open) => !open && stay()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Unsaved changes</AlertDialogTitle>
          <AlertDialogDescription>You have unsaved changes. Leave without saving?</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={stay}>Stay</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => {
              if (blocker.state === "blocked") blocker.proceed();
            }}
          >
            Leave
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
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
