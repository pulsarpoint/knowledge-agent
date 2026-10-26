import { useState, type FormEvent } from "react";
import { Check } from "lucide-react";
import { ApiError } from "~/lib/api";
import { DraftsUsed } from "./ConfirmDraftSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";
import { cn } from "~/lib/utils";

export interface Choice {
  id: string;
  name: string;
  /** A line under the name (a code project's Git source). */
  detail?: string;
}

/** A right-side sheet to pick one record (searched by name and detail) with an optional description; Add runs `add`
 * (a refusal stays shown), then `onAdded`. `drafts` names the drafts adding the chosen one confirms. */
export default function PickSheet({
  title,
  description,
  searchLabel,
  choices,
  drafts,
  add,
  onAdded,
  onClose,
}: {
  title: string;
  description?: string;
  searchLabel: string;
  choices: Choice[] | null;
  drafts?: (id: string | null) => string[];
  add: (id: string, description: string) => Promise<unknown>;
  onAdded: () => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needle = query.trim().toLowerCase();
  const shown = (choices ?? [])
    .filter((choice) => `${choice.name} ${choice.detail ?? ""}`.toLowerCase().includes(needle))
    .sort((a, b) => a.name.localeCompare(b.name));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!chosen) return;
    setSaving(true);
    setError(null);
    try {
      await add(chosen, text);
      onAdded();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not add it. Try again.");
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
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          {description && <SheetDescription>{description}</SheetDescription>}
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-4 p-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Input
            type="search"
            aria-label={searchLabel}
            placeholder={`${searchLabel}…`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {choices === null ? (
            <Spinner />
          ) : shown.length === 0 ? (
            <p className="text-sm text-muted-foreground">{choices.length === 0 ? "Nothing to add." : "No matches."}</p>
          ) : (
            <ul className="flex max-h-80 flex-col gap-1 overflow-y-auto">
              {shown.map((choice) => (
                <li key={choice.id}>
                  <button
                    type="button"
                    aria-pressed={chosen === choice.id}
                    disabled={saving}
                    onClick={() => setChosen(choice.id)}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded-md border px-3 py-2 text-left text-sm hover:bg-muted",
                      chosen === choice.id && "border-primary bg-muted",
                    )}
                  >
                    <span className="flex min-w-0 flex-col">
                      <span className="[overflow-wrap:anywhere]">{choice.name}</span>
                      {choice.detail && (
                        <span className="text-xs text-muted-foreground [overflow-wrap:anywhere]">{choice.detail}</span>
                      )}
                    </span>
                    {chosen === choice.id && <Check className="size-4 shrink-0" />}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <Field>
            <FieldLabel htmlFor="pick-description">Description (optional)</FieldLabel>
            <Textarea
              id="pick-description"
              maxLength={4000}
              rows={2}
              disabled={saving}
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </Field>
          {drafts && <DraftsUsed names={drafts(chosen)} />}
          <div className="flex gap-2">
            <Button disabled={saving || !chosen}>
              {saving && <Spinner data-icon="inline-start" />}
              Add
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
