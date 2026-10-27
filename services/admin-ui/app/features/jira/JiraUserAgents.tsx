import { useEffect, useState, type FormEvent } from "react";
import { X } from "lucide-react";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { getAgents } from "~/features/agents/api";
import {
  createAssigneeHandler,
  deleteJiraUserAgent,
  getJiraUserAgents,
  saveJiraUserAgent,
  searchJiraUsers,
  type AssigneeHandler,
  type JiraUser,
  type JiraUserAgent,
} from "./api";
import ConfirmRemove, { type Removal } from "~/components/ConfirmRemove";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Spinner } from "~/components/ui/spinner";

interface Agent {
  id: string;
  name: string;
  enabled: boolean;
}
const userLabel = (user: JiraUser) => `${user.display_name} (${user.key})`;
const message = (reason: unknown, fallback: string) => (reason instanceof ApiError ? reason.message : fallback);

function AgentSelect({
  agents,
  value,
  label,
  id,
  disabled,
  onChange,
}: {
  agents: Agent[];
  value: string;
  label: string;
  id?: string;
  disabled?: boolean;
  onChange: (agentId: string) => void;
}) {
  return (
    <Select value={value} disabled={disabled} onValueChange={onChange}>
      <SelectTrigger id={id} aria-label={label} className="w-48">
        <SelectValue placeholder="Choose an agent" />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {agents.map((agent) => (
            <SelectItem key={agent.id} value={agent.id}>
              {agent.name}
              {agent.enabled ? "" : " (disabled)"}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

/** Jira users → agents on the primary connection: a ticket assigned to a mapped user is that agent's work (a handler
 * targeting "Agent mapped to the assignee" runs it). Users are searched as you type; a typed key is accepted. */
export default function JiraUserAgents() {
  const [rows, setRows] = useState<JiraUserAgent[] | null>(null);
  const [handler, setHandler] = useState<AssigneeHandler | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [text, setText] = useState("");
  const [picked, setPicked] = useState<JiraUser | null>(null);
  const [found, setFound] = useState<JiraUser[]>([]);
  const [warning, setWarning] = useState<string | null>(null);
  const [agentId, setAgentId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getJiraUserAgents(controller.signal)
      .then((result) => {
        setRows(result.user_agents);
        setHandler(result.handler);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(message(reason, "Could not load the Jira users and agents."));
      });
    getAgents()
      .then((result) => setAgents(result.agents))
      .catch(() => setAgents([]));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const query = text.trim();
    if (picked || !query) {
      setFound([]);
      setWarning(null);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      searchJiraUsers(query, controller.signal)
        .then((result) => {
          setFound(result.users);
          setWarning(result.warning);
        })
        .catch((reason) => {
          if (!controller.signal.aborted) setWarning(message(reason, "Could not search Jira users."));
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [text, picked]);

  async function run(
    action: () => Promise<{ user_agents: JiraUserAgent[]; handler: AssigneeHandler | null } | void>,
    fallback: string,
  ) {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      if (result) {
        setRows(result.user_agents);
        setHandler(result.handler);
      }
      return true;
    } catch (reason) {
      setError(message(reason, fallback));
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function add(event: FormEvent) {
    event.preventDefault();
    const key = picked?.key ?? text.trim();
    if (!key || !agentId) return;
    const added = await run(
      () => saveJiraUserAgent(key, agentId, picked?.display_name ?? ""),
      "Could not save the Jira user.",
    );
    if (added) {
      setText("");
      setPicked(null);
      setAgentId("");
    }
  }
  const [removal, setRemoval] = useState<Removal | null>(null);
  const remove = (row: JiraUserAgent) =>
    run(async () => {
      await deleteJiraUserAgent(row.jira_user_key);
      setRows((current) => current?.filter((item) => item.jira_user_key !== row.jira_user_key) ?? null);
    }, "Could not remove the Jira user.");
  const createHandler = () =>
    run(async () => {
      setHandler((await createAssigneeHandler()).handler);
    }, "Could not create the handler.");
  // A row's agent stays selectable even when the agent list does not have it.
  const choices = (row: JiraUserAgent) =>
    agents.some((agent) => agent.id === row.agent_id)
      ? agents
      : [{ id: row.agent_id, name: row.agent_name, enabled: row.enabled }, ...agents];

  return (
    <section className="flex max-w-2xl flex-col gap-4 rounded-lg border p-6">
      <div className="flex flex-col gap-1">
        <h2 className="text-base font-medium">Users and agents</h2>
        <p className="text-sm text-muted-foreground">
          A ticket assigned to a mapped Jira user is that agent&apos;s work: an event handler that sends to{" "}
          <span className="font-medium">Agent mapped to the assignee</span> runs it.
        </p>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {rows === null ? null : handler ? (
        <p className="text-sm">
          Handled by:{" "}
          <Link
            className="text-primary underline underline-offset-4"
            to={`/events/handlers?handler=${encodeURIComponent(handler.id)}`}
          >
            {handler.name}
          </Link>{" "}
          ({handler.enabled ? "enabled" : "disabled"})
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-muted-foreground">No handler runs these agents.</p>
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void createHandler()}>
            Create handler
          </Button>
        </div>
      )}
      {rows === null ? null : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No Jira users are mapped to agents yet.</p>
      ) : (
        <ul className="flex flex-col divide-y">
          {rows.map((row) => (
            <li key={row.jira_user_key} className="flex flex-wrap items-center gap-3 py-2">
              <div className="flex min-w-40 flex-1 flex-col">
                <span className="text-sm font-medium">{row.jira_user_name}</span>
                <span className="text-xs text-muted-foreground">{row.jira_user_key}</span>
              </div>
              <AgentSelect
                agents={choices(row)}
                value={row.agent_id}
                label={`Agent for ${row.jira_user_name}`}
                disabled={busy}
                onChange={(agent) =>
                  void run(
                    () => saveJiraUserAgent(row.jira_user_key, agent, row.jira_user_name),
                    "Could not change the agent.",
                  )
                }
              />
              {!row.enabled && <Badge variant="secondary">Agent disabled</Badge>}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                disabled={busy}
                aria-label={`Remove ${row.jira_user_name}`}
                onClick={() =>
                  setRemoval({
                    title: `Remove “${row.jira_user_name}”?`,
                    description: `Tickets assigned to this Jira user no longer go to ${row.agent_name}. The agent stays.`,
                    action: "Remove Jira user",
                    run: () => void remove(row),
                  })
                }
              >
                <X />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="flex flex-wrap items-end gap-3">
        <Field className="min-w-56 flex-1">
          <FieldLabel htmlFor="jira-user-search">Jira user</FieldLabel>
          <Input
            id="jira-user-search"
            autoComplete="off"
            maxLength={255}
            value={text}
            placeholder="Search by name or type a user key"
            onChange={(event) => {
              setText(event.target.value);
              setPicked(null);
            }}
          />
          {found.length > 0 && (
            <ul aria-label="Matching Jira users" className="flex flex-col rounded-md border">
              {found.map((user) => (
                <li key={user.key}>
                  <Button
                    type="button"
                    variant="ghost"
                    className="w-full justify-start"
                    onClick={() => {
                      setPicked(user);
                      setText(userLabel(user));
                    }}
                  >
                    {userLabel(user)}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {warning && <FieldDescription className="text-amber-700 dark:text-amber-400">{warning}</FieldDescription>}
        </Field>
        <Field className="w-auto">
          <FieldLabel htmlFor="jira-user-agent">Agent</FieldLabel>
          <AgentSelect agents={agents} value={agentId} label="Agent" id="jira-user-agent" onChange={setAgentId} />
        </Field>
        <Button type="submit" disabled={busy || !text.trim() || !agentId}>
          {busy && <Spinner data-icon="inline-start" />}Add
        </Button>
      </form>
      <ConfirmRemove removal={removal} onClose={() => setRemoval(null)} />
    </section>
  );
}
