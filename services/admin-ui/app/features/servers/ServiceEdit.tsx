import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "~/lib/api";
import { createItem, isStale, updateItem } from "./inventory";
import type { ServiceAsset } from "./services";
import { AllServices, useServicePage } from "./ServiceLayout";
import { servicePath } from "./ServicePanel";
import ServiceFields, { serviceFields, serviceValue, type ServiceFieldValues } from "./ServiceFields";
import LeaveGuard from "~/components/LeaveGuard";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { FieldGroup, FieldSet } from "~/components/ui/field";
import { Spinner } from "~/components/ui/spinner";

const same = (a: ServiceFieldValues, b: ServiceFieldValues) =>
  (Object.keys(a) as (keyof ServiceFieldValues)[]).every((key) => a[key] === b[key]);

/** The service's own fields edited in place: Save (sends the revision it read; a stale one reloads and says so), or
 * Discard to drop the changes. Leaving with unsaved changes asks first. */
export default function ServiceEdit() {
  const { service, changed, conflict, setNotice } = useServicePage();
  const [started, setStarted] = useState(0);
  return (
    // Discard starts the form afresh; a save does when the reloaded service comes with its new revision (so does a
    // change by the chat's agent, dropping unsaved edits).
    <ServiceForm
      key={`${service.revision}:${started}`}
      service={service}
      save={(value) => updateItem("services", service.id, service.revision, value)}
      onDiscard={() => setStarted((n) => n + 1)}
      onSaved={() => {
        changed();
        setNotice("Service saved.");
      }}
      onStale={conflict}
    />
  );
}

/** `/services/new`: the Edit page empty; Save creates the service and opens its View. */
export function ServiceNew() {
  const navigate = useNavigate();
  const [created, setCreated] = useState<string | null>(null);
  // Navigates once the form has rendered as saved, so its leave guard lets the page go.
  useEffect(() => {
    if (created) navigate(servicePath(created));
  }, [created]);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <AllServices />
        <h2 className="text-xl font-semibold">New service</h2>
      </div>
      <ServiceForm
        service={null}
        saved={created !== null}
        save={(value) => createItem<ServiceAsset>("services", value)}
        onSaved={(item) => setCreated(item.id)}
      />
    </div>
  );
}

function ServiceForm({
  service,
  saved = false,
  save,
  onSaved,
  onDiscard,
  onStale,
}: {
  service: ServiceAsset | null;
  /** The new service is saved: nothing is unsaved any more. */
  saved?: boolean;
  save: (value: ReturnType<typeof serviceValue>) => Promise<{ id: string }>;
  onSaved: (item: { id: string }) => void;
  onDiscard?: () => void;
  onStale?: () => void;
}) {
  const initial = serviceFields(service);
  const [value, setValue] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = !saved && !same(value, initial);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onSaved(await save(serviceValue(value)));
    } catch (e) {
      if (isStale(e) && onStale) onStale();
      else setError(e instanceof ApiError ? e.message : "Could not save the service. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex max-w-2xl flex-col gap-6">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <FieldSet disabled={saving || saved}>
        <FieldGroup>
          <ServiceFields idPrefix="service" value={value} onChange={setValue} />
        </FieldGroup>
      </FieldSet>
      <div className="flex gap-2">
        <Button disabled={saving || saved || !value.name.trim() || (service !== null && !dirty)}>
          {saving && <Spinner data-icon="inline-start" />}
          Save
        </Button>
        {onDiscard && (
          <Button type="button" variant="outline" disabled={saving || !dirty} onClick={onDiscard}>
            Discard
          </Button>
        )}
      </div>
      <LeaveGuard dirty={dirty} />
    </form>
  );
}
