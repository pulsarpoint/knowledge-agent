import { useState } from "react";
import { useSearchParams } from "react-router";
import { Sparkles } from "lucide-react";
import { ApiError } from "~/lib/api";
import { resourceWord, startSession, type AgentOptions, type ResourceType } from "./api";
import { OWN, startInput, useAgentSettings } from "./agentSettings";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

/** The library prompts a discovery run prefers, by name: the seeded Server discovery and Code project overview. */
const DISCOVERY_PROMPTS = ["Server discovery", "Code project overview"];
/** The library prompt a discovery run starts from: a preferred one when the library has it, else the chat's default. */
const discoveryPrompt = (options: AgentOptions, fallback: string) =>
  options.prompts.find((item) => DISCOVERY_PROMPTS.includes(item.name))?.id ?? fallback;

/** Discover…: a run on the resource from a library prompt (its text can be edited first), which saves what it finds as
 * drafts (inventory entries and documents) for a person to confirm. Start starts it and opens it as the page's chat
 * (`?chat=<id>`). */
export default function DiscoverWizard({
  resource,
  blockedReason = null,
}: {
  resource: { type: ResourceType; id: string; name: string };
  blockedReason?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<{ key: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [, setParams] = useSearchParams();
  const loaded = useAgentSettings(open ? { kind: resource.type, id: resource.id, name: resource.name } : null);
  const { options, settings } = loaded;
  const key = choice?.key ?? (options && settings ? discoveryPrompt(options, settings.promptKey) : OWN);
  const text =
    choice?.text ??
    (key === OWN ? (settings?.systemPrompt ?? "") : (options?.prompts.find((p) => p.id === key)?.content ?? ""));
  const input =
    options && settings
      ? startInput(
          { kind: resource.type, id: resource.id, name: resource.name },
          options,
          { ...settings, promptKey: key, systemPrompt: text },
          `Discover this ${resourceWord(resource.type)}.`,
        )
      : null;

  const reset = () => {
    setChoice(null);
    setError("");
  };
  async function start() {
    if (!input || busy) return;
    setBusy(true);
    setError("");
    try {
      const started = await startSession(input);
      setOpen(false);
      reset();
      setParams(
        (current) => {
          const params = new URLSearchParams(current);
          params.set("chat", started.id);
          return params;
        },
        { replace: true },
      );
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "The run could not start.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
        <Sparkles data-icon="inline-start" />
        Discover…
      </Button>
      <Sheet
        open={open}
        onOpenChange={(value) => {
          if (busy) return;
          setOpen(value);
          if (!value) reset();
        }}
      >
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>Discover {resource.name}</SheetTitle>
            <SheetDescription>
              {`A run that explores the ${resourceWord(resource.type)} and saves what it finds as drafts: inventory entries and documents. You confirm or discard them.`}
            </SheetDescription>
          </SheetHeader>
          <FieldGroup className="mt-4">
            {options && (
              <>
                <Field>
                  <FieldLabel htmlFor="discover-prompt-choice">Prompt</FieldLabel>
                  <Select
                    value={key}
                    onValueChange={(value) =>
                      setChoice({
                        key: value,
                        text: value === OWN ? text : (options.prompts.find((p) => p.id === value)?.content ?? ""),
                      })
                    }
                  >
                    <SelectTrigger id="discover-prompt-choice" aria-label="Prompt">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {options.prompts.map((item) => (
                          <SelectItem key={item.id} value={item.id}>
                            {item.name}
                          </SelectItem>
                        ))}
                        <SelectItem value={OWN}>Own prompt</SelectItem>
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </Field>
                <Field>
                  <FieldLabel htmlFor="discover-prompt">Prompt text</FieldLabel>
                  <Textarea
                    id="discover-prompt"
                    rows={16}
                    className="font-mono text-xs"
                    value={text}
                    onChange={(e) => setChoice({ key: choice?.key ?? key, text: e.target.value })}
                  />
                  <FieldDescription>
                    The run's system prompt. Edit it as you like. The chat settings of this{" "}
                    {resourceWord(resource.type)} give the rest (model, resources).
                  </FieldDescription>
                </Field>
              </>
            )}
            {loaded.error ? (
              <Alert variant="destructive">
                <AlertDescription>{loaded.error}</AlertDescription>
              </Alert>
            ) : (
              !input && <p className="text-sm text-muted-foreground">Loading…</p>
            )}
            {blockedReason && (
              <Alert>
                <AlertDescription>{blockedReason}</AlertDescription>
              </Alert>
            )}
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <div className="flex justify-end">
              <Button
                type="button"
                disabled={busy || !input || !text.trim() || blockedReason !== null}
                onClick={() => void start()}
              >
                {busy && <Spinner data-icon="inline-start" />}
                Start
              </Button>
            </div>
          </FieldGroup>
        </SheetContent>
      </Sheet>
    </>
  );
}
