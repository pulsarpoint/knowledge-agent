import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "~/lib/api";
import { createSkill, importGit, importZip, previewGit, previewZip, type FoundSkill, type GitInput } from "./skills";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Textarea } from "~/components/ui/textarea";

const message = (error: unknown, fallback: string) => (error instanceof ApiError ? error.message : fallback);

/** New skill: a name and a description make the SKILL.md template; the editor opens on it. */
export function NewSkillSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const skill = await createSkill({ name: name.trim(), description: description.trim() });
      navigate(`/skills/${encodeURIComponent(skill.id)}`);
    } catch (reason) {
      setError(message(reason, "Could not create this skill."));
      setSaving(false);
    }
  }
  return (
    <Sheet open={open} onOpenChange={(value) => !value && !saving && onClose()}>
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>New skill</SheetTitle>
          <SheetDescription>
            Creates a SKILL.md with this name and description, and opens it in the editor.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-col gap-4">
          {error && (
            <Alert variant="destructive">
              <AlertDescription role="alert">{error}</AlertDescription>
            </Alert>
          )}
          <FieldSet disabled={saving}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="new-skill-name">Name</FieldLabel>
                <Input
                  id="new-skill-name"
                  required
                  maxLength={64}
                  pattern="[a-z0-9]+(-[a-z0-9]+)*"
                  placeholder="pdf-forms"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
                <FieldDescription>Lowercase letters, digits and single hyphens.</FieldDescription>
              </Field>
              <Field>
                <FieldLabel htmlFor="new-skill-description">Description</FieldLabel>
                <Textarea
                  id="new-skill-description"
                  required
                  rows={3}
                  maxLength={1024}
                  placeholder="Use when filling in PDF forms."
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                />
                <FieldDescription>When an agent should use this skill.</FieldDescription>
              </Field>
            </FieldGroup>
          </FieldSet>
          <SheetFooter>
            <Button disabled={saving}>
              {saving && <Spinner data-icon="inline-start" />}
              Create and edit
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function Preview({ skills }: { skills: FoundSkill[] }) {
  return (
    <ul aria-label="Skills found" className="flex flex-col gap-2">
      {skills.map((skill) => (
        <li key={skill.name} className="rounded-md border p-3">
          <p className="font-medium">{skill.name}</p>
          <p className="text-sm text-muted-foreground">{skill.description}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {skill.files.length} {skill.files.length === 1 ? "file" : "files"}
          </p>
        </li>
      ))}
    </ul>
  );
}

/** Import: a zip or a git folder is checked first (the skills it holds are listed), then imported on confirm. */
export function ImportSkillsSheet({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported: (names: string[]) => void;
}) {
  const [tab, setTab] = useState("zip");
  const [file, setFile] = useState<File | null>(null);
  const [git, setGit] = useState<GitInput>({ url: "", ref: "", path: "" });
  const [found, setFound] = useState<FoundSkill[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(work: () => Promise<void>, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (reason) {
      setError(message(reason, fallback));
    } finally {
      setBusy(false);
    }
  }
  const check = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      if (tab === "zip") {
        if (file) setFound((await previewZip(file)).skills);
      } else setFound((await previewGit({ ...git, url: git.url.trim() })).skills);
    }, "Could not read these skills.");
  };
  const confirm = () =>
    void run(async () => {
      const { skills } = tab === "zip" ? await importZip(file!) : await importGit({ ...git, url: git.url.trim() });
      setFound(null);
      setFile(null);
      onImported(skills.map((skill) => skill.name));
    }, "Could not import these skills.");
  const reset = () => {
    setFound(null);
    setError(null);
  };

  return (
    <Sheet open={open} onOpenChange={(value) => !value && !busy && onClose()}>
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Import skills</SheetTitle>
          <SheetDescription>
            A skill is a folder with a SKILL.md. A zip may hold one skill, or a folder per skill.
          </SheetDescription>
        </SheetHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription role="alert">{error}</AlertDescription>
          </Alert>
        )}
        <Tabs
          value={tab}
          onValueChange={(value) => {
            setTab(value);
            reset();
          }}
        >
          <TabsList>
            <TabsTrigger value="zip" disabled={busy}>
              Upload a zip
            </TabsTrigger>
            <TabsTrigger value="git" disabled={busy}>
              From git
            </TabsTrigger>
          </TabsList>
          <TabsContent value="zip">
            <form onSubmit={check} className="flex flex-col gap-4">
              <Field>
                <FieldLabel htmlFor="skill-zip">Zip file</FieldLabel>
                <Input
                  id="skill-zip"
                  type="file"
                  accept=".zip,application/zip"
                  disabled={busy}
                  onChange={(event) => {
                    setFile(event.target.files?.[0] ?? null);
                    reset();
                  }}
                />
                <FieldDescription>Up to 25 MB.</FieldDescription>
              </Field>
              <div>
                <Button variant="outline" disabled={busy || !file}>
                  {busy && found === null && <Spinner data-icon="inline-start" />}
                  Check archive
                </Button>
              </div>
            </form>
          </TabsContent>
          <TabsContent value="git">
            <form onSubmit={check} className="flex flex-col gap-4">
              <FieldSet disabled={busy}>
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor="skill-git-url">Repository URL</FieldLabel>
                    <Input
                      id="skill-git-url"
                      required
                      placeholder="https://github.com/acme/skills"
                      value={git.url}
                      onChange={(event) => {
                        setGit({ ...git, url: event.target.value });
                        reset();
                      }}
                    />
                    <FieldDescription>
                      A public repository, or one whose code project has a git credential.
                    </FieldDescription>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="skill-git-ref">Ref</FieldLabel>
                    <Input
                      id="skill-git-ref"
                      placeholder="The default branch"
                      value={git.ref}
                      onChange={(event) => {
                        setGit({ ...git, ref: event.target.value });
                        reset();
                      }}
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="skill-git-path">Path</FieldLabel>
                    <Input
                      id="skill-git-path"
                      placeholder="skills/pdf-forms"
                      value={git.path}
                      onChange={(event) => {
                        setGit({ ...git, path: event.target.value });
                        reset();
                      }}
                    />
                    <FieldDescription>The skill's folder in the repository; empty for its root.</FieldDescription>
                  </Field>
                </FieldGroup>
              </FieldSet>
              <div>
                <Button variant="outline" disabled={busy || !git.url.trim()}>
                  {busy && found === null && <Spinner data-icon="inline-start" />}
                  Check repository
                </Button>
              </div>
            </form>
          </TabsContent>
        </Tabs>
        {found && (
          <>
            <Preview skills={found} />
            <SheetFooter>
              <Button disabled={busy || found.length === 0} onClick={confirm}>
                {busy && <Spinner data-icon="inline-start" />}
                Import {found.length} {found.length === 1 ? "skill" : "skills"}
              </Button>
            </SheetFooter>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
