import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowLeft } from "lucide-react";
import { getAgent, type AgentProfile } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import AgentProfileSettings from "./AgentProfileSettings";
import AgentResources from "./AgentResources";
import AgentTestView from "./AgentTestView";
import AgentPromptCard from "./AgentPromptCard";
import AgentToolsView from "./AgentToolsView";
import AgentActions from "./AgentActions";

export default function AgentDetailView({ agentId }: { agentId: string }) {
  const [params, setParams] = useSearchParams();
  const [profile, setProfile] = useState<AgentProfile>();
  const sections = ["general", "resources", "tools", "test"];
  const selected = params.get("view") ?? "";
  const view = sections.includes(selected) ? selected : "general";
  const [error, setError] = useState("");
  const [profileDirty, setProfileDirty] = useState(false);
  const [resourcesDirty, setResourcesDirty] = useState(false);
  const [toolsDirty, setToolsDirty] = useState(false);
  const [toolsOpened, setToolsOpened] = useState(false);
  useEffect(() => {
    if (view === "tools") setToolsOpened(true);
  }, [view]);
  const [reload, setReload] = useState(0);
  const [promptDirty, setPromptDirty] = useState(false);
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
  }, [agentId, reload]);
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
            <Button variant="outline" onClick={() => setReload((n) => n + 1)}>
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
              <AgentActions profile={profile} onChanged={() => setReload((n) => n + 1)} />
            </div>
            {profile.description && view !== "test" && (
              <p className="text-sm text-muted-foreground">{profile.description}</p>
            )}
          </div>
          <Tabs
            value={view}
            onValueChange={(next) =>
              setParams((current) => {
                current.set("view", next);
                return current;
              })
            }
            className="flex flex-col gap-6"
          >
            <div className="overflow-x-auto">
              <TabsList aria-label="Agent sections" className="w-max">
                {sections.map((section) => (
                  <TabsTrigger key={section} value={section}>
                    {section[0].toUpperCase() + section.slice(1)}
                  </TabsTrigger>
                ))}
              </TabsList>
            </div>
            <TabsContent value={view} forceMount className="flex flex-col gap-6">
              {view === "general" && profile.original && (
                <p className="text-sm text-muted-foreground">
                  Clone of{" "}
                  <Link
                    className="underline underline-offset-4"
                    to={`/agents/${encodeURIComponent(profile.original.id)}`}
                  >
                    {profile.original.name}
                  </Link>
                </p>
              )}
              {toolsOpened && (
                <div hidden={view !== "tools"}>
                  <AgentToolsView
                    key={reload}
                    agentId={agentId}
                    revision={profile.revision}
                    onDirtyChange={setToolsDirty}
                    onSaved={(revision) => setProfile((current) => current && { ...current, revision })}
                  />
                </div>
              )}
              <div hidden={view !== "general"} className="flex flex-col gap-6">
                <AgentProfileSettings
                  key={reload}
                  profile={profile}
                  onSaved={setProfile}
                  onDirtyChange={setProfileDirty}
                />
                <AgentPromptCard profile={profile} onSaved={setProfile} onDirtyChange={setPromptDirty} />
              </div>
              <div hidden={view !== "resources"}>
                <AgentResources key={reload} profile={profile} onSaved={setProfile} onDirtyChange={setResourcesDirty} />
              </div>
              <div hidden={view !== "test"}>
                <AgentTestView
                  agentId={agentId}
                  agentName={profile.name}
                  available={profile.enabled}
                  ready
                  dirty={profileDirty || promptDirty || resourcesDirty}
                  active={view === "test"}
                  revision={profile.revision}
                  onAgentChanged={() => setReload((n) => n + 1)}
                />
              </div>
            </TabsContent>
          </Tabs>
        </>
      )}
    </section>
  );
}
