import { useEffect, useId, useState } from "react";
import { Link } from "react-router";
import { Sparkles } from "lucide-react";
import {
  createWizardAgent,
  generateAgent,
  getWizardOptions,
  type AgentProposal,
  type WizardOptions,
  type WizardResource,
} from "./wizard";
import { ACCESS_TYPES, CONTENT_EDITOR_HELP, countsSummary, type AccessType } from "./api";
import { PROMPT_LIMIT } from "./AgentPromptCard";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "~/components/ui/card";
import { Checkbox } from "~/components/ui/checkbox";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldDescription,
  FieldSet,
  FieldLegend,
  FieldContent,
} from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectItem } from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "~/components/ui/tabs";
import { Textarea } from "~/components/ui/textarea";
import { toast } from "sonner";

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const id = useId();
  return (
    <Field orientation="horizontal">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
    </Field>
  );
}

function Selection({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: WizardResource[];
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const id = useId();
  return (
    <FieldSet>
      <FieldLegend>{label}</FieldLegend>
      <FieldGroup className="max-h-72 overflow-y-auto">
        {options.map((option) => (
          <Field key={option.id} orientation="horizontal">
            <Checkbox
              id={`${id}-${option.id}`}
              checked={selected.includes(option.id)}
              onCheckedChange={(checked) =>
                onChange(checked ? [...selected, option.id] : selected.filter((item) => item !== option.id))
              }
            />
            <FieldContent>
              <FieldLabel htmlFor={`${id}-${option.id}`}>{option.name}</FieldLabel>
              {option.description && <FieldDescription>{option.description}</FieldDescription>}
            </FieldContent>
          </Field>
        ))}
        {options.length === 0 && (
          <FieldDescription>No available resources. Add them in administration first.</FieldDescription>
        )}
      </FieldGroup>
    </FieldSet>
  );
}

function blankProposal(model_id: string | null): AgentProposal {
  return {
    name: "",
    description: "",
    when_to_use: "",
    system_prompt: "",
    instructions: "",
    model_id,
    enabled: true,
    access: { resources: [], roles: { content_editor: false } },
  };
}

/** The catalog list a type of resource is picked from, with a line that tells them apart. */
function catalogList(catalog: WizardOptions["catalog"], type: AccessType): WizardResource[] {
  switch (type) {
    case "server":
      return catalog.servers.map((s) => ({
        ...s,
        description: [s.hostname, s.description].filter(Boolean).join(" · "),
      }));
    case "service":
      return catalog.services;
    case "environment":
      return catalog.environments;
    case "code_project":
      return catalog.code_projects.map((p) => ({
        ...p,
        description: [p.description, p.folder_paths.join(", "), p.remote_url].filter(Boolean).join(" · "),
      }));
    case "collection":
      return catalog.collections;
    case "document":
      return catalog.documents.map((d) => ({ id: d.id, name: d.title, description: d.description }));
    case "skill":
      return catalog.skills;
    case "mcp_server":
      return catalog.mcp_servers;
  }
}

export default function AgentWizard({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [options, setOptions] = useState<WizardOptions>();
  const [role, setRole] = useState("");
  const [model, setModel] = useState("");
  const [agentModel, setAgentModel] = useState<string | null>(null); // the model the new agent runs on
  const [proposal, setProposal] = useState<AgentProposal>();
  const [step, setStep] = useState<"describe" | "review">("describe");
  const [tab, setTab] = useState("general");
  const [busy, setBusy] = useState<"generate" | "create" | null>(null);
  const [error, setError] = useState("");
  const [showContext, setShowContext] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let live = true;
    getWizardOptions()
      .then((data) => {
        if (!live) return;
        setOptions(data);
        setError("");
        setModel((current) =>
          data.catalog.models.some((m) => m.id === current) ? current : (data.catalog.models[0]?.id ?? ""),
        );
        setAgentModel((current) =>
          data.catalog.models.some((m) => m.id === current) ? current : (data.catalog.models[0]?.id ?? null),
        );
      })
      .catch((reason) => {
        if (live) setError(reason.message);
      });
    return () => {
      live = false;
    };
  }, [reload]);
  const catalog = options?.catalog;
  const agentModelField = (
    <Field>
      <FieldLabel htmlFor="wizard-agent-model">Agent model</FieldLabel>
      <Select value={agentModel ?? "none"} onValueChange={(value) => setAgentModel(value === "none" ? null : value)}>
        <SelectTrigger id="wizard-agent-model">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="none">No model (choose later)</SelectItem>
            {(catalog?.models ?? []).map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.name} · {m.model}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      <FieldDescription>The model this agent runs on. An agent runs only after a model is selected.</FieldDescription>
    </Field>
  );
  function edit(value: Partial<AgentProposal>) {
    setProposal((current) => (current ? { ...current, ...value } : current));
  }
  /** The picked ids of one type: kept ones stay in place, new ones are added at the end (a document read-only). */
  function pick(type: AccessType, ids: string[]) {
    if (!proposal) return;
    const kept = proposal.access.resources.filter((item) => item.type !== type || ids.includes(item.id));
    const added = ids
      .filter((id) => !kept.some((item) => item.type === type && item.id === id))
      .map((id) => (type === "document" ? { type, id, editable: false } : { type, id }));
    edit({ access: { ...proposal.access, resources: [...kept, ...added] } });
  }
  async function generate() {
    setBusy("generate");
    setError("");
    try {
      const data = await generateAgent(role, model);
      setProposal({ ...data.proposal, model_id: agentModel });
      setStep("review");
      setTab("general");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not generate an agent.");
    } finally {
      setBusy(null);
    }
  }
  function startBlank() {
    setError("");
    setProposal(blankProposal(agentModel));
    setStep("review");
    setTab("general");
  }
  async function create() {
    if (!proposal) return;
    if (!proposal.name.trim() || !proposal.system_prompt.trim()) {
      setError("Name and system prompt are required.");
      setTab(!proposal.system_prompt.trim() ? "prompt" : "general");
      return;
    }
    setBusy("create");
    setError("");
    try {
      const saved = await createWizardAgent({ ...proposal, model_id: agentModel });
      toast.success(`Agent ${proposal.name.trim()} created`);
      onCreated(saved.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create the agent.");
    } finally {
      setBusy(null);
    }
  }
  return (
    <Card aria-label="New Agent Wizard">
      <CardHeader>
        <CardTitle>New Agent Wizard</CardTitle>
        <CardDescription>
          {step === "describe"
            ? "1. Describe the role and let AI propose a configuration from your resources, or start blank and fill it in yourself."
            : "2. Review and edit. Nothing is saved until you create the agent."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6 pb-6">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>
              {error}
              {!options && (
                <Button variant="outline" size="sm" onClick={() => setReload((n) => n + 1)}>
                  Retry
                </Button>
              )}
            </AlertDescription>
          </Alert>
        )}
        {!options || !catalog ? (
          <Skeleton className="h-48 w-full" />
        ) : (
          <fieldset disabled={busy !== null} className="min-w-0">
            {step === "describe" ? (
              <FieldGroup>
                <div className="grid gap-4 sm:grid-cols-2">{agentModelField}</div>
                <Field>
                  <FieldLabel htmlFor="wizard-role">What should this agent do?</FieldLabel>
                  <Textarea
                    id="wizard-role"
                    autoFocus
                    rows={5}
                    maxLength={20000}
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                    placeholder="Create a payments investigator that reads the payments code and runbooks, checks staging logs over SSH, and explains failures with evidence. Do not allow sudo."
                  />
                  <FieldDescription>
                    Describe its role, expected output, resources, and any restrictions. Do not include passwords or
                    private keys.
                  </FieldDescription>
                </Field>
                <Field>
                  <FieldLabel htmlFor="wizard-generator-model">Model for generating the configuration</FieldLabel>
                  <Select value={model} onValueChange={setModel}>
                    <SelectTrigger id="wizard-generator-model">
                      <SelectValue placeholder="Select a model" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {catalog.models.map((m) => (
                          <SelectItem key={m.id} value={m.id}>
                            {m.name} · {m.model}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                  {catalog.models.length === 0 && (
                    <FieldDescription>
                      <Link className="underline" to="/llm-settings">
                        Add an enabled model in LLM Settings
                      </Link>{" "}
                      to generate a proposal.
                    </FieldDescription>
                  )}
                </Field>
                <FieldSet>
                  <FieldLegend>Available context</FieldLegend>
                  <FieldDescription>
                    The model receives resource names, descriptions, folder paths, and saved credential references.
                    Private keys and tokens are excluded. It designs the agent without accessing these resources.
                  </FieldDescription>
                  <div className="flex flex-wrap gap-2">
                    {(
                      [
                        ["Code projects", catalog.code_projects.length],
                        ["Collections", catalog.collections.length],
                        ["Documents", catalog.documents.length],
                        ["Servers", catalog.servers.length],
                        ["Environments", catalog.environments.length],
                        ["Skills", catalog.skills.length],
                        ["MCP servers", catalog.mcp_servers.length],
                        ["Services", catalog.services.length],
                      ] as const
                    ).map(([label, count]) => (
                      <Badge key={label} variant="secondary">
                        {count} {label}
                      </Badge>
                    ))}
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    className="w-fit"
                    onClick={() => setShowContext(!showContext)}
                  >
                    {showContext ? "Hide design context" : "Show design context"}
                  </Button>
                  {showContext && (
                    <Field>
                      <FieldLabel htmlFor="wizard-context">
                        Generation prompt, configuration schema, and resource catalog
                      </FieldLabel>
                      <Textarea
                        id="wizard-context"
                        className="font-mono text-xs"
                        readOnly
                        rows={14}
                        value={JSON.stringify(
                          {
                            instructions: options.prompt,
                            output_schema: options.schema,
                            task: { role, generation_model_id: model, catalog, output_schema: options.schema },
                          },
                          null,
                          2,
                        )}
                      />
                    </Field>
                  )}
                </FieldSet>
              </FieldGroup>
            ) : (
              proposal && (
                <Tabs value={tab} onValueChange={setTab}>
                  <TabsList className="mb-4 h-auto flex-wrap justify-start">
                    {[
                      ["general", "General"],
                      ["prompt", "Prompt"],
                      ["resources", "Resources"],
                    ].map(([value, label]) => (
                      <TabsTrigger key={value} value={value}>
                        {label}
                      </TabsTrigger>
                    ))}
                  </TabsList>
                  <TabsContent value="general">
                    <FieldGroup>
                      <p className="text-sm">
                        <span className="font-medium" aria-label="Chosen model">
                          {catalog.models.find((m) => m.id === agentModel)?.name ?? "No model"}
                        </span>{" "}
                        <span className="text-muted-foreground">Change it on page 1 with Edit role.</span>
                      </p>
                      <Field>
                        <FieldLabel htmlFor="wizard-name">Agent name</FieldLabel>
                        <Input
                          id="wizard-name"
                          maxLength={100}
                          value={proposal.name}
                          onChange={(e) => edit({ name: e.target.value })}
                        />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor="wizard-purpose">Purpose</FieldLabel>
                        <Textarea
                          id="wizard-purpose"
                          maxLength={4000}
                          value={proposal.description}
                          onChange={(e) => edit({ description: e.target.value })}
                        />
                      </Field>
                      <Field>
                        <FieldLabel htmlFor="wizard-when">When to use this agent</FieldLabel>
                        <Textarea
                          id="wizard-when"
                          maxLength={4000}
                          value={proposal.when_to_use}
                          onChange={(e) => edit({ when_to_use: e.target.value })}
                        />
                      </Field>
                      <Toggle
                        label="Enable agent"
                        checked={proposal.enabled}
                        onChange={(enabled) => edit({ enabled })}
                      />
                      <Field>
                        <FieldLabel htmlFor="wizard-instructions">Additional instructions</FieldLabel>
                        <Textarea
                          id="wizard-instructions"
                          rows={5}
                          maxLength={40000}
                          value={proposal.instructions}
                          onChange={(e) => edit({ instructions: e.target.value })}
                        />
                      </Field>
                      <Field orientation="horizontal">
                        <Switch
                          id="wizard-content-editor"
                          checked={proposal.access.roles.content_editor}
                          onCheckedChange={(content_editor) =>
                            edit({ access: { ...proposal.access, roles: { content_editor } } })
                          }
                        />
                        <FieldContent>
                          <FieldLabel htmlFor="wizard-content-editor">Content editor</FieldLabel>
                          <FieldDescription>{CONTENT_EDITOR_HELP}</FieldDescription>
                        </FieldContent>
                      </Field>
                      <FieldSet>
                        <FieldLegend>Proposed resources</FieldLegend>
                        <p className="text-sm" aria-label="Proposed resources">
                          {countsSummary(
                            Object.fromEntries(
                              ACCESS_TYPES.map(({ type }) => [
                                type,
                                proposal.access.resources.filter((item) => item.type === type).length,
                              ]),
                            ),
                          )}
                        </p>
                        <FieldDescription>Change them on the Resources tab.</FieldDescription>
                      </FieldSet>
                    </FieldGroup>
                  </TabsContent>
                  <TabsContent value="prompt">
                    <FieldGroup>
                      <Field>
                        <FieldLabel htmlFor="wizard-prompt">System prompt</FieldLabel>
                        <Textarea
                          id="wizard-prompt"
                          rows={16}
                          maxLength={PROMPT_LIMIT}
                          value={proposal.system_prompt}
                          onChange={(e) => edit({ system_prompt: e.target.value })}
                        />
                        <FieldDescription>
                          Required. Saved as this agent’s system prompt; edit it later on its General tab.
                        </FieldDescription>
                      </Field>
                    </FieldGroup>
                  </TabsContent>
                  <TabsContent value="resources">
                    <FieldGroup>
                      {ACCESS_TYPES.map(({ type, label }) => {
                        const options = catalogList(catalog, type);
                        const picked = proposal.access.resources.filter((item) => item.type === type);
                        return (
                          <FieldGroup key={type}>
                            <Selection
                              label={label}
                              options={options}
                              selected={picked.map((item) => item.id)}
                              onChange={(ids) => pick(type, ids)}
                            />
                            {type === "document" &&
                              picked.map((item) => (
                                <Toggle
                                  key={item.id}
                                  label={`Editable ${options.find((option) => option.id === item.id)?.name ?? item.id}`}
                                  checked={Boolean(item.editable)}
                                  onChange={(editable) =>
                                    edit({
                                      access: {
                                        ...proposal.access,
                                        resources: proposal.access.resources.map((entry) =>
                                          entry.type === "document" && entry.id === item.id
                                            ? { ...entry, editable }
                                            : entry,
                                        ),
                                      },
                                    })
                                  }
                                />
                              ))}
                          </FieldGroup>
                        );
                      })}
                    </FieldGroup>
                  </TabsContent>
                </Tabs>
              )
            )}
          </fieldset>
        )}
      </CardContent>
      <CardFooter className="flex-wrap gap-2">
        {step === "describe" ? (
          <>
            <Button disabled={busy !== null || !role.trim() || !model || !options} onClick={() => void generate()}>
              {busy === "generate" ? <Spinner data-icon="inline-start" /> : <Sparkles data-icon="inline-start" />}
              {busy === "generate" ? "Generating proposal…" : proposal ? "Generate new proposal" : "Generate proposal"}
            </Button>
            <Button variant="outline" disabled={busy !== null || !options} onClick={startBlank}>
              Start blank
            </Button>
            {proposal && (
              <Button variant="outline" disabled={busy !== null} onClick={() => setStep("review")}>
                Back to proposal
              </Button>
            )}
          </>
        ) : (
          <>
            <Button
              disabled={busy !== null || !proposal?.name.trim() || !proposal?.system_prompt.trim()}
              onClick={() => void create()}
            >
              {busy === "create" && <Spinner data-icon="inline-start" />}
              {busy === "create" ? "Creating…" : "Create agent"}
            </Button>
            <Button
              variant="outline"
              disabled={busy !== null}
              onClick={() => {
                setError("");
                setStep("describe");
              }}
            >
              Edit role
            </Button>
          </>
        )}
        <Button variant="ghost" disabled={busy !== null} onClick={onClose}>
          Cancel
        </Button>
      </CardFooter>
    </Card>
  );
}
