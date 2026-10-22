import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "~/lib/api";
import { getRepositories } from "~/features/codeProjects/api";
import { createItem, type RelationKind } from "./inventory";
import type { ServiceAsset } from "./services";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

const KINDS: Record<RelationKind, string> = { depends_on: "Depends on", part_of: "Part of", built_from: "Built from" };

/** Adds a relation from the service: depends on or part of another service, or built from a code project. */
export default function AddRelationDialog({
  serviceId,
  services,
  kind: initialKind,
  onSaved,
  onClose,
}: {
  serviceId: string;
  services: ServiceAsset[];
  kind: RelationKind;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<RelationKind>(initialKind);
  const [target, setTarget] = useState("");
  const [description, setDescription] = useState("");
  const [repositories, setRepositories] = useState<{ id: string; name: string }[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getRepositories(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setRepositories(value.repositories);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof ApiError ? e.message : "Could not load the code projects.");
      });
    return () => controller.abort();
  }, []);

  const codeProject = kind === "built_from";
  const choices = (
    codeProject
      ? (repositories ?? [])
      : services.filter((service) => service.id !== serviceId).map(({ id, name }) => ({ id, name }))
  ).sort((a, b) => a.name.localeCompare(b.name));
  const service = services.find((item) => item.id === serviceId)?.name ?? "this service";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await createItem("relations", {
        from_service_id: serviceId,
        kind,
        to_service_id: codeProject ? null : target,
        to_code_project_id: codeProject ? target : null,
        description,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not add the relation. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <SheetContent className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Add relation</SheetTitle>
          <SheetDescription>What {service} depends on, is part of, or is built from.</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-6 p-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <FieldSet disabled={saving}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="relation-kind">Kind</FieldLabel>
                <Select
                  value={kind}
                  onValueChange={(value) => {
                    setKind(value as RelationKind);
                    setTarget("");
                  }}
                >
                  <SelectTrigger id="relation-kind" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {(Object.keys(KINDS) as RelationKind[]).map((value) => (
                        <SelectItem key={value} value={value}>
                          {KINDS[value]}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel htmlFor="relation-target">{codeProject ? "Code project" : "Service"}</FieldLabel>
                <Select value={target} onValueChange={setTarget}>
                  <SelectTrigger id="relation-target" className="w-full">
                    <SelectValue placeholder={codeProject ? "Choose a code project" : "Choose a service"} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {choices.map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.name}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel htmlFor="relation-description">Description</FieldLabel>
                <Textarea
                  id="relation-description"
                  maxLength={4000}
                  rows={2}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </Field>
            </FieldGroup>
          </FieldSet>
          <div className="flex gap-2">
            <Button disabled={saving || !target}>
              {saving && <Spinner data-icon="inline-start" />}
              Add relation
            </Button>
            <Button type="button" variant="outline" disabled={saving} onClick={onClose}>
              Cancel
            </Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
