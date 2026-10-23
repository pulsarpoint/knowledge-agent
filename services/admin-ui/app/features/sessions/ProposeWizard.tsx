import { useId, useState } from "react";
import { useSearchParams } from "react-router";
import { Sparkles } from "lucide-react";
import { ApiError } from "~/lib/api";
import { startSession, type ResourceType, type StartInput } from "./api";
import { startInput, useAgentSettings, type AgentSettings } from "./agentSettings";
import type { AgentOptions } from "./api";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";
import { cn } from "~/lib/utils";

export type ProposalAspect = "configuration" | "logs" | "health checks" | "ports and paths";
export const PROPOSAL_ASPECTS: { value: ProposalAspect; label: string }[] = [
  { value: "configuration", label: "Configuration" },
  { value: "logs", label: "Logs" },
  { value: "health checks", label: "Health checks" },
  { value: "ports and paths", label: "Ports and paths" },
];
/** What the Propose wizard was asked for. `services` counts only for a server. */
export interface ProposalOptions {
  services: boolean;
  documents: boolean;
  overview: boolean;
  /** One document per service: servers and environments only. */
  perService: boolean;
  aspects: ProposalAspect[];
  extra: string;
}
export const DEFAULT_PROPOSAL_OPTIONS: ProposalOptions = {
  services: true,
  documents: true,
  overview: true,
  perService: true,
  aspects: PROPOSAL_ASPECTS.map((item) => item.value),
  extra: "",
};
/** The options as they apply to a resource type: a service or an environment proposes documents only, and a service has
 * no "one document per service". */
export function applicable(type: ResourceType, options: ProposalOptions): ProposalOptions {
  return {
    ...options,
    services: type === "server" && options.services,
    perService: type !== "service" && options.perService,
  };
}

const READ: Record<ResourceType, string> = {
  server: "get_resource for the server, then for its services",
  service: "get_resource for the service, then for the servers it is deployed on",
  environment: "get_resource for the environment, then for its services and servers",
};
const ON: Record<ResourceType, string> = {
  server: "that server",
  service: "a server it is deployed on",
  environment: "its servers",
};
const OVERVIEW: Record<ResourceType, string> = {
  server: "its role, operating system, the services on it and how they connect, ports, important paths and logs",
  service: "what it does, where it runs and how it connects to other services",
  environment: "what it is for, its services and servers and how they connect",
};

/** The prompt of a Propose wizard run: the Server discovery prompt's steps for the resource type, one block per chosen
 * option, then the extra instructions. */
export function buildProposalPrompt(type: ResourceType, given: ProposalOptions): string {
  const options = applicable(type, given);
  const steps = [
    `Read what is already recorded first: ${READ[type]}. Read the documents that matter with get_document.`,
    `Explore with read-only commands only, with ssh_run on ${ON[type]}: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.`,
    "Never copy passwords, keys, tokens or other secrets into a proposal. Describe how something is configured instead.",
  ];
  if (options.services)
    steps.push(
      "Save every service you find that is not recorded yet as a draft with save_inventory_draft (or save_service and save_deployment), with its deployment on this server, what it connects to and what it depends on. A person confirms or discards the drafts. Do not change entries that are already recorded.",
    );
  if (options.documents) {
    if (options.overview)
      steps.push(`Propose one overview document for the ${type} with propose_document: ${OVERVIEW[type]}.`);
    if (options.perService)
      steps.push(
        `Propose one document for each significant service${type === "environment" ? " in the environment" : ""} (about_service): what it does and how it runs.`,
      );
    const aspects = PROPOSAL_ASPECTS.filter((item) => options.aspects.includes(item.value)).map((item) => item.value);
    if (aspects.length > 0) steps.push(`Cover in every document: ${aspects.join(", ")}.`);
  }
  steps.push(
    "When a save or a proposal is refused, the result says why: correct it and call again.",
    "Finish with a short report: what you found, what you proposed and what you could not check.",
  );
  const text = `Explore the ${type} in the Resource line and propose what the inventory should record about it.\n\n${steps
    .map((step, index) => `${index + 1}. ${step}`)
    .join("\n")}`;
  const extra = options.extra.trim();
  return extra ? `${text}\n\nExtra instructions:\n${extra}` : text;
}

/** The run's first message (and so its title). */
export function proposalMessage(type: ResourceType, given: ProposalOptions): string {
  const options = applicable(type, given);
  const what =
    options.services && options.documents ? "services and documents" : options.services ? "services" : "documents";
  return `Propose ${what} for this ${type}.`;
}

/** Whether a step's choices are enough to go on. */
function complete(type: ResourceType, step: Step, given: ProposalOptions): boolean {
  const options = applicable(type, given);
  if (step === "what") return options.services || options.documents;
  if (step === "options") return !options.documents || options.overview || options.perService;
  return true;
}

/** The start request of a wizard run: the resource chat's settings (model, resources, experts), the generated prompt as
 * the system prompt (no library prompt), the content editor role (the run may propose) and the tag `proposals`. */
export function wizardInput(
  resource: { type: ResourceType; id: string; name: string },
  agentOptions: AgentOptions,
  settings: AgentSettings,
  options: ProposalOptions,
  prompt: string,
): StartInput {
  const scope = { kind: resource.type, id: resource.id, name: resource.name };
  const base = startInput(scope, agentOptions, settings, proposalMessage(resource.type, options));
  return {
    ...base,
    prompt_id: null,
    system_prompt: prompt.trim(),
    content_editor: true,
    tags: ["proposals"],
  };
}

type Step = "what" | "options" | "review";
const STEPS: { value: Step; label: string }[] = [
  { value: "what", label: "What" },
  { value: "options", label: "Options" },
  { value: "review", label: "Review" },
];

function Choice({
  label,
  checked,
  onChange,
  description,
}: {
  label: string;
  checked: boolean;
  onChange: (on: boolean) => void;
  description?: string;
}) {
  const id = useId();
  return (
    <Field orientation="horizontal">
      <Checkbox id={id} checked={checked} onCheckedChange={(on) => onChange(on === true)} />
      <div className="flex flex-col gap-0.5">
        <FieldLabel htmlFor={id} className="font-normal">
          {label}
        </FieldLabel>
        {description && <FieldDescription>{description}</FieldDescription>}
      </div>
    </Field>
  );
}

/** Propose…: a three-step wizard (What, Options, Review) whose result is a run on the resource that saves services as
 * drafts (servers only) and proposes documents. Start starts it and opens it as the page's chat (`?chat=<id>`). */
export default function ProposeWizard({
  resource,
  blockedReason = null,
}: {
  resource: { type: ResourceType; id: string; name: string };
  blockedReason?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<Step>("what");
  const [options, setOptions] = useState<ProposalOptions>(DEFAULT_PROPOSAL_OPTIONS);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [, setParams] = useSearchParams();
  const settings = useAgentSettings(open ? { kind: resource.type, id: resource.id, name: resource.name } : null);
  const type = resource.type;
  const shown = applicable(type, options);
  const change = (patch: Partial<ProposalOptions>) => setOptions((current) => ({ ...current, ...patch }));
  const input =
    settings.options && settings.settings
      ? wizardInput(resource, settings.options, settings.settings, options, prompt)
      : null;

  const reset = () => {
    setStep("what");
    setOptions(DEFAULT_PROPOSAL_OPTIONS);
    setPrompt("");
    setError("");
  };
  const next = () => {
    if (step === "what") setStep("options");
    else if (step === "options") {
      setPrompt(buildProposalPrompt(type, options)); // Review starts from the options chosen now
      setStep("review");
    }
  };
  const back = () => setStep(step === "review" ? "options" : "what");
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
        Propose…
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
            <SheetTitle>Propose for {resource.name}</SheetTitle>
            <SheetDescription>
              {type === "server"
                ? "A run that explores the server, saves the services it finds as drafts and proposes documents for you to review."
                : `A run that proposes documents about the ${type} for you to review.`}
            </SheetDescription>
          </SheetHeader>
          <ol aria-label="Steps" className="mt-4 flex flex-wrap gap-2 text-sm">
            {STEPS.map((item, index) => (
              <li
                key={item.value}
                aria-current={item.value === step ? "step" : undefined}
                className={cn(
                  "rounded-full border px-3 py-1",
                  item.value === step ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground",
                )}
              >
                <span aria-hidden>{`${index + 1}. `}</span>
                {item.label}
              </li>
            ))}
          </ol>
          <FieldGroup className="mt-4">
            {step === "what" && (
              <FieldSet>
                <FieldLegend>What should the run propose?</FieldLegend>
                {type === "server" && (
                  <Choice
                    label="Services on this server"
                    description="New services it finds, saved as drafts for you to confirm."
                    checked={options.services}
                    onChange={(services) => change({ services })}
                  />
                )}
                <Choice
                  label="Documents"
                  description={`Documents about the ${type}${type === "service" ? "" : " and its services"}.`}
                  checked={options.documents}
                  onChange={(documents) => change({ documents })}
                />
              </FieldSet>
            )}
            {step === "options" && (
              <>
                {shown.services && (
                  <FieldSet>
                    <FieldLegend>Services</FieldLegend>
                    <FieldDescription>
                      Every new service it finds on this server, with its deployment, connections and relations, is
                      saved as drafts in one go. You confirm or discard them in the environment workspace.
                    </FieldDescription>
                  </FieldSet>
                )}
                {shown.documents && (
                  <FieldSet>
                    <FieldLegend>Documents</FieldLegend>
                    <Choice
                      label={`An overview document of the ${type}`}
                      checked={options.overview}
                      onChange={(overview) => change({ overview })}
                    />
                    {type !== "service" && (
                      <Choice
                        label="One document per service"
                        checked={options.perService}
                        onChange={(perService) => change({ perService })}
                      />
                    )}
                    <FieldSet>
                      <FieldLegend variant="label">Aspects to cover</FieldLegend>
                      {PROPOSAL_ASPECTS.map((aspect) => (
                        <Choice
                          key={aspect.value}
                          label={aspect.label}
                          checked={options.aspects.includes(aspect.value)}
                          onChange={(on) =>
                            change({
                              aspects: on
                                ? [...options.aspects, aspect.value]
                                : options.aspects.filter((item) => item !== aspect.value),
                            })
                          }
                        />
                      ))}
                    </FieldSet>
                  </FieldSet>
                )}
                <Field>
                  <FieldLabel htmlFor="propose-extra">Extra instructions</FieldLabel>
                  <Textarea
                    id="propose-extra"
                    rows={3}
                    value={options.extra}
                    placeholder="Optional. For example: the backups run from cron."
                    onChange={(e) => change({ extra: e.target.value })}
                  />
                </Field>
              </>
            )}
            {step === "review" && (
              <>
                <Field>
                  <FieldLabel htmlFor="propose-prompt">Prompt</FieldLabel>
                  <Textarea
                    id="propose-prompt"
                    rows={16}
                    className="font-mono text-xs"
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                  />
                  <FieldDescription>The run's system prompt. Edit it as you like.</FieldDescription>
                </Field>
                {settings.error ? (
                  <Alert variant="destructive">
                    <AlertDescription>{settings.error}</AlertDescription>
                  </Alert>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {input
                      ? `The agent gets every tool and may propose changes for your review. The chat settings of this ${type} for the rest (model, resources).`
                      : "Loading…"}
                  </p>
                )}
                {blockedReason && (
                  <Alert>
                    <AlertDescription>{blockedReason}</AlertDescription>
                  </Alert>
                )}
              </>
            )}
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <div className="flex justify-between gap-2">
              <Button type="button" variant="ghost" disabled={step === "what" || busy} onClick={back}>
                Back
              </Button>
              {step === "review" ? (
                <Button
                  type="button"
                  disabled={busy || !input || !prompt.trim() || blockedReason !== null}
                  onClick={() => void start()}
                >
                  {busy && <Spinner data-icon="inline-start" />}
                  Start
                </Button>
              ) : (
                <Button type="button" disabled={!complete(type, step, options)} onClick={next}>
                  Next
                </Button>
              )}
            </div>
          </FieldGroup>
        </SheetContent>
      </Sheet>
    </>
  );
}
