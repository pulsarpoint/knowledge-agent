import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { X } from "lucide-react";
import { ApiError } from "~/lib/api";
import { saveEnvironment, type Environment, type EnvironmentAsset } from "./environments";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

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
    <form onSubmit={submit} className="flex flex-col gap-6 rounded-lg border p-6">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">{environment ? "Edit environment" : "Add environment"}</h2>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Close environment form"
          disabled={saving}
          onClick={onClose}
        >
          <X />
        </Button>
      </div>
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
      <div className="flex gap-2">
        <Button type="submit" disabled={saving}>
          {saving && <Spinner data-icon="inline-start" />}
          {environment ? "Save changes" : "Save environment"}
        </Button>
        <Button type="button" variant="outline" disabled={saving} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
