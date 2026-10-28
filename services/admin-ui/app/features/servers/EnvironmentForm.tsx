import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { ApiError } from "~/lib/api";
import { saveEnvironment, type Environment, type EnvironmentAsset } from "./environments";
import LeaveGuard, { useSheetGuard } from "~/components/LeaveGuard";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

/** New environment / Edit environment in a right Sheet: Create environment (or Save) and Cancel; closing it or leaving
 * the page with unsaved changes asks Stay / Leave. */
export default function EnvironmentForm({
  environment,
  onSaved,
  onClose,
}: {
  environment: EnvironmentAsset | null;
  onSaved: (environment: Environment) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(environment?.name ?? "");
  const [description, setDescription] = useState(environment?.description ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = !saving && (name !== (environment?.name ?? "") || description !== (environment?.description ?? ""));
  const guard = useSheetGuard(dirty);
  const close = () => guard.close(onClose);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await saveEnvironment(
        {
          name,
          description,
          ...(environment ? { revision: environment.revision } : {}),
        },
        environment?.id,
      );
      onSaved(saved);
    } catch (error) {
      setError(error instanceof ApiError ? error.message : "Could not save the environment. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet open onOpenChange={(next) => !next && close()}>
      <LeaveGuard dirty={dirty} />
      {guard.dialog}
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{environment ? "Edit environment" : "New environment"}</SheetTitle>
          <SheetDescription>A name such as Development, Staging or Production, and what it is for.</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-6">
          {error && (
            <Alert variant="destructive">
              <AlertTitle>Environment could not be saved</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <FieldSet disabled={saving}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="environment-name">Environment name</FieldLabel>
                <Input
                  id="environment-name"
                  required
                  maxLength={100}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Development, Staging, Production…"
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="environment-description">Description (optional)</FieldLabel>
                <Textarea
                  id="environment-description"
                  maxLength={4000}
                  rows={3}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="What this environment is used for and any useful context."
                />
              </Field>
            </FieldGroup>
          </FieldSet>
          <p className="text-sm text-muted-foreground">
            A service or server is in this environment when one of its deployments is: add deployments on the{" "}
            <Link className="underline" to="/services">
              Services page
            </Link>
            .
          </p>
          <SheetFooter className="gap-2 sm:justify-start sm:space-x-0">
            <Button type="submit" disabled={saving}>
              {saving && <Spinner data-icon="inline-start" />}
              {environment ? "Save" : "Create environment"}
            </Button>
            <Button type="button" variant="outline" disabled={saving} onClick={close}>
              Cancel
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
