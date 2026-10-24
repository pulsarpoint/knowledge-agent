import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import type { AgentSummary } from "./api";
import AgentSettingsSheet from "~/features/sessions/AgentSettingsSheet";
import {
  defaultSettings,
  pickReason,
  settingsSummary,
  startInput,
  type AgentSettings,
} from "~/features/sessions/agentSettings";
import { getSessionOptions, startSession, type AgentOptions, type ChatScope } from "~/features/sessions/api";
import OutputSchemaPicker from "~/features/outputSchemas/OutputSchemaPicker";
import { Alert, AlertDescription } from "~/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

/** Run agent: start a chat (a chat session) with a saved, enabled agent and open it on its session page. Every resource
 * of the agent is given unless narrowed with the Test tab's settings sheet; an output schema makes it finish with JSON.
 * `agentId` preselects the agent (a row's Run). */
export default function RunAgentDialog({
  agents,
  agentId,
  onClose,
}: {
  agents: AgentSummary[];
  agentId: string;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const enabled = agents.filter((agent) => agent.enabled);
  const [chosen, setChosen] = useState(agentId);
  const [message, setMessage] = useState("");
  const [loaded, setLoaded] = useState<{ agentId: string; options: AgentOptions; settings: AgentSettings } | null>(
    null,
  );
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!chosen) return;
    const controller = new AbortController();
    setLoaded(null);
    setError("");
    getSessionOptions({ agentId: chosen }, controller.signal)
      .then((options) => {
        if (!controller.signal.aborted) setLoaded({ agentId: chosen, options, settings: defaultSettings(options) });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the agent.");
      });
    return () => controller.abort();
  }, [chosen]);
  const current = loaded?.agentId === chosen ? loaded : null;
  const agent = enabled.find((item) => item.id === chosen);
  const scope: ChatScope | null = agent ? { kind: "agent", id: agent.id, name: agent.name } : null;
  const reason = current ? pickReason(current.options, current.settings) : null;
  const ready = !busy && !!scope && !!current && !reason && !!message.trim();
  async function start() {
    if (!scope || !current) return;
    setBusy(true);
    setError("");
    try {
      const started = await startSession({
        ...startInput(scope, current.options, current.settings, message),
        purpose: "chat",
      });
      navigate(`/sessions/${encodeURIComponent(started.id)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the agent.");
      setBusy(false);
    }
  }
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Run agent</AlertDialogTitle>
          <AlertDialogDescription>Start a chat with a saved agent. It opens on its own page.</AlertDialogDescription>
        </AlertDialogHeader>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="run-agent">Agent</FieldLabel>
            <Select value={chosen} onValueChange={setChosen} disabled={busy}>
              <SelectTrigger id="run-agent" aria-label="Agent">
                <SelectValue placeholder="Choose an agent" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {enabled.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor="run-agent-message">First message</FieldLabel>
            <Textarea
              id="run-agent-message"
              aria-label="First message"
              rows={4}
              disabled={busy}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
          </Field>
          {current && (
            <Field>
              <FieldLabel>Resources</FieldLabel>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm">{settingsSummary(current.options, current.settings, { output: false })}</p>
                <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => setPicking(true)}>
                  Choose resources
                </Button>
              </div>
              {reason && <FieldDescription className="text-destructive">{reason}</FieldDescription>}
            </Field>
          )}
          {current && (
            <OutputSchemaPicker
              idPrefix="run-agent"
              value={current.settings.outputSchema ?? null}
              disabled={busy}
              onChange={(outputSchema) =>
                setLoaded((state) => state && { ...state, settings: { ...state.settings, outputSchema } })
              }
            />
          )}
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </FieldGroup>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <Button type="button" disabled={!ready} onClick={() => void start()}>
            {busy && <Spinner data-icon="inline-start" />}Start
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
      {scope && current && (
        <AgentSettingsSheet
          open={picking}
          onOpenChange={setPicking}
          scope={scope}
          options={current.options}
          settings={current.settings}
          onChange={(patch) => setLoaded((state) => state && { ...state, settings: { ...state.settings, ...patch } })}
          previewMessage={message}
          session={null}
          onStartNew={() => setPicking(false)}
          showOutputSchema={false}
        />
      )}
    </AlertDialog>
  );
}
