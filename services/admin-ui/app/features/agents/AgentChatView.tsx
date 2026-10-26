import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { useAgentSettings } from "~/features/sessions/agentSettings";
import type { ChatScope } from "~/features/sessions/api";
import AgentChat from "~/features/sessions/AgentChat";

/** The agent's Chat page: the shared agent chat for this saved agent, full width. The first message starts a run of the
 * agent (narrowed by the settings sheet); the run is kept in the URL (/agents/<id>/chat?session_id=…). */
export default function AgentChatView({
  agentId,
  agentName,
  available,
  revision,
  onAgentChanged,
}: {
  agentId: string;
  agentName: string;
  available: boolean;
  revision?: number;
  onAgentChanged?: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const scope: ChatScope = { kind: "agent", id: agentId, name: agentName };
  const settings = useAgentSettings(scope, revision);
  // The run the chat shows, with the agent it belongs to (a run reported for another agent never reaches this one's URL).
  const [shown, setShown] = useState<{ agentId: string; id: string | null }>({ agentId, id: params.get("session_id") });
  // The URL follows the shown run (a reload reopens it).
  useEffect(() => {
    if (shown.agentId !== agentId || (params.get("session_id") ?? null) === shown.id) return;
    setParams(
      (current) => {
        if (shown.id) current.set("session_id", shown.id);
        else current.delete("session_id");
        return current;
      },
      { replace: true },
    );
  }, [agentId, shown, params]); // eslint-disable-line react-hooks/exhaustive-deps

  const blockedReason = available ? null : "This agent is disabled. Enable it in General to run a task.";
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
