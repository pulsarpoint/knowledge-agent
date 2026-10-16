import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { useAgentSettings } from "~/features/sessions/agentSettings";
import type { ChatScope } from "~/features/sessions/api";
import AgentChat from "~/features/sessions/AgentChat";

/** The agent Test tab: the shared agent chat for this saved agent, full width. The first message starts a run of the
 * agent (narrowed by the settings sheet); the run is kept in the URL (?view=test&session_id=…). Settings load only while
 * the tab is shown; the chat mounts the first time it is. */
export default function AgentTestView({
  agentId,
  agentName,
  available,
  dirty,
  active,
  revision,
  onAgentChanged,
}: {
  agentId: string;
  agentName: string;
  available: boolean;
  dirty: boolean;
  active: boolean;
  revision?: number;
  onAgentChanged?: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const scope: ChatScope = { kind: "agent", id: agentId, name: agentName };
  const settings = useAgentSettings(active ? scope : null, revision);
  const [seen, setSeen] = useState(active);
  // The run the chat shows, with the agent it belongs to (a run reported for another agent never reaches this one's URL).
  const [shown, setShown] = useState<{ agentId: string; id: string | null }>({ agentId, id: params.get("session_id") });
  useEffect(() => {
    if (active) setSeen(true);
  }, [active]);
  // The URL follows the shown run while the tab is shown (a reload reopens it).
  useEffect(() => {
    if (
      !active ||
      shown.agentId !== agentId ||
      params.get("view") !== "test" ||
      (params.get("session_id") ?? null) === shown.id
    )
      return;
    setParams(
      (current) => {
        if (shown.id) current.set("session_id", shown.id);
        else current.delete("session_id");
        return current;
      },
      { replace: true },
    );
  }, [active, agentId, shown, params]); // eslint-disable-line react-hooks/exhaustive-deps

  const blockedReason = !available
    ? "This agent is disabled. Enable it in General to run a task."
    : dirty
      ? "Save or discard changes in the configuration tabs before starting a task."
      : null;
  if (!seen) return null;
  return (
    <AgentChat
      key={agentId}
      scope={scope}
      settings={settings}
      initialRunId={params.get("session_id")}
      onRunChange={(id) => setShown({ agentId, id })}
      blockedReason={blockedReason}
      onAgentChanged={onAgentChanged}
    />
  );
}
