import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useOutletContext, useParams } from "react-router";
import { ArrowLeft } from "lucide-react";
import { getAgent, type AgentProfile } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { cn } from "~/lib/utils";
import AgentActions from "./AgentActions";

/** What the agent's sub-pages get from the layout: the agent (loaded once), a setter for a saved one, and a reload
 * (after Clone, Enable, … in the header); `reloaded` counts reloads (sub-pages remount their forms on it). */
export interface AgentPageContext {
  profile: AgentProfile;
  setProfile: (profile: AgentProfile) => void;
  reload: () => void;
  reloaded: number;
}
export const useAgentPage = () => useOutletContext<AgentPageContext>();

const SECTIONS = [
  { page: "general", label: "General" },
  { page: "resources", label: "Resources" },
  { page: "chat", label: "Chat" },
];

/** The agent page (`/agents/:agentId`): the header (All agents, name and badges, actions, description) and the
 * sections as links, above the open sub-page (General, Resources or Chat). */
export default function AgentLayout() {
  const { agentId } = useParams();
  return <AgentPage key={agentId} agentId={agentId!} />;
}

function AgentPage({ agentId }: { agentId: string }) {
  const { search } = useLocation();
  const [profile, setProfile] = useState<AgentProfile>();
  const [error, setError] = useState("");
  const [reloaded, setReloaded] = useState(0);
  const reload = () => setReloaded((n) => n + 1);
  useEffect(() => {
    let live = true;
    getAgent(agentId)
      .then((value) => {
        if (live) {
          setProfile(value);
          setError("");
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [agentId, reloaded]);
  return (
    <section className="flex flex-col gap-6" aria-label="Agent details">
      <Button asChild variant="link" className="self-start px-0">
        <Link to="/agents">
          <ArrowLeft />
          All agents
        </Link>
      </Button>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>
            {error}
            <Button variant="outline" onClick={reload}>
              Reload agent
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {!profile ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <>
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-xl font-semibold">{profile.name}</h2>
                {profile.expert && <Badge variant="secondary">Expert</Badge>}
                {!profile.enabled && <Badge variant="outline">Disabled</Badge>}
              </div>
              <AgentActions profile={profile} onChanged={reload} />
            </div>
            {profile.description && <p className="text-sm text-muted-foreground">{profile.description}</p>}
          </div>
          {/* The links keep the query: a chat's open run (?session_id=) is there again when coming back to Chat. */}
          <nav aria-label="Agent sections" className="overflow-x-auto">
            <div className="inline-flex h-10 w-max items-center rounded-md bg-muted p-1 text-muted-foreground">
              {SECTIONS.map(({ page, label }) => (
                <NavLink
                  key={page}
                  to={{ pathname: page, search }}
                  className={({ isActive }) =>
                    cn(
                      "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                      isActive && "bg-background text-foreground shadow-sm",
                    )
                  }
                >
                  {label}
                </NavLink>
              ))}
            </div>
          </nav>
          <Outlet context={{ profile, setProfile, reload, reloaded } satisfies AgentPageContext} />
        </>
      )}
    </section>
  );
}
