import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Link } from "react-router";
import { PlugZap, RefreshCw } from "lucide-react";
import { ApiError } from "~/lib/api";
import { getJiraSettings, saveJiraSettings, testJiraConnection, type JiraConnection } from "./api";
import JiraUserAgents from "./JiraUserAgents";
import LeaveGuard from "~/components/LeaveGuard";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";

export default function JiraSettingsView() {
  const [connection, setConnection] = useState<JiraConnection | null | undefined>(undefined);
  const [baseUrl, setBaseUrl] = useState("");
  const [username, setUsername] = useState("");
  const [token, setToken] = useState("");
  const [removeToken, setRemoveToken] = useState(false);
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getJiraSettings(controller.signal)
      .then(({ connection }) => {
        if (!controller.signal.aborted) {
          setConnection(connection);
          setBaseUrl(connection?.base_url ?? "");
          setUsername(connection?.username ?? "");
          setToken("");
          setRemoveToken(false);
          setError(null);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setError(error instanceof ApiError ? error.message : "Could not load Jira settings.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);
  const dirty =
    baseUrl !== (connection?.base_url ?? "") ||
    username !== (connection?.username ?? "") ||
    token !== "" ||
    removeToken;
  const needsToken = !connection?.has_token || baseUrl !== connection.base_url || username !== connection.username;
  const disabled = busy !== null || loading;

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy("save");
    setError(null);
    try {
      const result = await saveJiraSettings({
        base_url: baseUrl,
        username,
        ...(connection ? { revision: connection.revision } : {}),
        ...(removeToken ? { token: null } : token ? { token } : {}),
      });
      setConnection(result.connection);
      setBaseUrl(result.connection.base_url);
      setUsername(result.connection.username);
      setToken("");
      setRemoveToken(false);
      toast.success("Jira settings saved.");
    } catch (error) {
      setError(error instanceof ApiError ? error.message : "Could not save Jira settings.");
    } finally {
      setBusy(null);
    }
  }
  async function test() {
    setBusy("test");
    setError(null);
    try {
      const result = await testJiraConnection();
      toast.success(`Connected as ${result.user.display_name} (${result.user.username}). Ticket search is accessible.`);
    } catch (error) {
      setError(error instanceof ApiError ? error.message : "Could not test the Jira connection.");
    } finally {
      try {
        setConnection((await getJiraSettings()).connection);
      } catch {
        /* Keep the operation result if the status refresh fails. */
      }
      setBusy(null);
    }
  }
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap justify-between gap-3">
        <p className="text-sm text-muted-foreground">Jira Data Center · Personal access token</p>
        <Button variant="outline" disabled={disabled} onClick={() => setRefresh((value) => value + 1)}>
          <RefreshCw data-icon="inline-start" />
          Reload settings
        </Button>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Jira request failed</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {connection === undefined && loading && <Skeleton className="h-64 w-full" />}
      {connection !== undefined && (
        <form onSubmit={save} className="flex max-w-2xl flex-col gap-6 rounded-lg border p-6">
          <LeaveGuard dirty={dirty} />
          <FieldSet disabled={disabled}>
            <FieldLegend>Server connection</FieldLegend>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="jira-url">Jira server URL</FieldLabel>
                <Input
                  id="jira-url"
                  type="url"
                  required
                  maxLength={1000}
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder="https://jira.company.internal/jira"
                />
                <FieldDescription>
                  Include a context path such as /jira if your installation uses one. For Jira running on this computer,
                  use host.docker.internal instead of localhost.
                </FieldDescription>
              </Field>
              <Field>
                <FieldLabel htmlFor="jira-username">Jira username</FieldLabel>
                <Input
                  id="jira-username"
                  required
                  maxLength={200}
                  autoComplete="off"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="integration-user"
                />
                <FieldDescription>
                  The login name of the token owner. The connection test verifies this identity.
                </FieldDescription>
              </Field>
              <Field>
                <FieldLabel htmlFor="jira-token">Personal access token</FieldLabel>
                <Input
                  id="jira-token"
                  type="password"
                  autoComplete="new-password"
                  required={needsToken && !removeToken}
                  disabled={disabled || removeToken}
                  maxLength={4096}
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  placeholder={
                    connection?.has_token ? "Saved token — leave blank to keep it" : "Paste your Jira Data Center token"
                  }
                />
                <FieldDescription>
                  {connection?.has_token
                    ? "A token is stored. Enter a new token to replace it."
                    : "Create a personal access token in your Jira profile."}{" "}
                  Token values are encrypted and never returned by the API.
                </FieldDescription>
              </Field>
              {connection?.has_token && (
                <Field orientation="horizontal">
                  <Checkbox
                    id="jira-remove-token"
                    checked={removeToken}
                    disabled={disabled}
                    onCheckedChange={(checked) => {
                      setRemoveToken(checked === true);
                      setToken("");
                    }}
                  />
                  <FieldLabel htmlFor="jira-remove-token">Remove saved token</FieldLabel>
                </Field>
              )}
            </FieldGroup>
          </FieldSet>
          {connection && needsToken && (
            <p className="text-sm text-muted-foreground">
              Changing the server or username requires the token again and clears the previous ticket list.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={disabled || !dirty}>
              {busy === "save" && <Spinner data-icon="inline-start" />}Save settings
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={disabled || dirty || !connection?.has_token}
              onClick={test}
            >
              {busy === "test" ? <Spinner data-icon="inline-start" /> : <PlugZap data-icon="inline-start" />}Test
              connection
            </Button>
            <Button asChild type="button" variant="ghost">
              <Link to={`/jira/tickets`}>View tickets</Link>
            </Button>
          </div>
          {dirty && <p className="text-sm text-muted-foreground">Save changes before testing the connection.</p>}
          {connection?.last_tested_at && (
            <p className="text-sm text-muted-foreground">
              Last check: {connection.last_test_error ? "Failed" : "Connected"} ·{" "}
              {new Date(connection.last_tested_at).toLocaleString()}
              {connection.last_test_error && <> · {connection.last_test_error}</>}
            </p>
          )}
        </form>
      )}
      {connection && <JiraUserAgents key={connection.id} />}
    </div>
  );
}
