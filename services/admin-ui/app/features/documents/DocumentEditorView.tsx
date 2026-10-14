import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import { ArrowLeft, Bot, Save, Trash2 } from "lucide-react";
import {
  deleteDocument,
  exportDocument,
  folderError,
  getDocument,
  getDocumentResources,
  getDocumentVersion,
  saveDocument,
  type DocumentDetail,
  type DocumentInput,
  type DocumentResources,
  type DocumentVersion,
} from "./api";
import { agentRunPath } from "~/features/sessions/api";
import { useAgentSettings } from "~/features/sessions/agentSettings";
import { useWidePage } from "~/lib/widePage";
import type { SessionDetail } from "~/features/sessions/api";
import { createDraft, draftPath, promotedNotice, updateDraft, type PromoteResult } from "./drafts";
import AgentDraftBar from "./AgentDraftBar";
import AgentChat from "~/features/sessions/AgentChat";
import DocumentAgentRuns from "./DocumentAgentRuns";
import DocumentConnections from "./DocumentConnections";
import DocumentDrafts from "./DocumentDrafts";
import Markdown from "~/components/Markdown";
import DocumentResourceSelection from "./DocumentResourceSelection";
import UnsavedDocumentChanges from "./UnsavedDocumentChanges";
import { Alert, AlertDescription } from "~/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel, FieldDescription } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Textarea } from "~/components/ui/textarea";
import { DOCUMENT_KINDS, emptyIds, idsOf, pickIds } from "~/lib/resources";

const TABS = ["write", "preview", "resources", "history", "drafts", "connections", "agent-runs"];

const PANE_KEY = "ask-agent-pane";
function readPane() {
  try {
    return localStorage.getItem(PANE_KEY);
  } catch {
    return null;
  }
}
function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* not remembered */
  }
}

/** The editor. On the New page, Write with an agent shows a Folder field (where the agent writes the document);
 * onFieldsChange reports the Title and Folder, and registerLeave lets the page leave without the unsaved-changes question
 * (a promoted agent draft opens the new document). */
function Editor({
  document,
  resources,
  onSaved,
  onDraftSaved,
  onPromoted,
  agentOpen,
  onToggleAgent,
  registerConfirm,
  onFieldsChange,
  registerLeave,
}: {
  document: DocumentDetail | null;
  resources: DocumentResources;
  onSaved: (id: string) => void;
  onDraftSaved: (draftId: string) => void;
  onPromoted: (result: PromoteResult) => void;
  agentOpen: boolean;
  onToggleAgent: () => void;
  registerConfirm: (confirm: (run: () => void) => void) => void;
  onFieldsChange: (title: string, folder: string) => void;
  registerLeave: (leave: () => void) => void;
}) {
  const navigate = useNavigate();
  const [initial] = useState<DocumentInput>(() =>
    document
      ? {
          slug: document.slug,
          title: document.title,
          description: document.description,
          purpose: document.purpose,
          audience: document.audience,
          tags: document.tags,
          markdown: document.current_version.markdown,
          ...pickIds(DOCUMENT_KINDS, document),
          revision: document.revision,
          source_commits: Object.fromEntries(
            document.current_version.source_snapshot.code_projects
              .filter((r) => r.git_commit)
              .map((r) => [r.id, r.git_commit!]),
          ),
        }
      : {
          slug: "",
          title: "",
          description: "",
          purpose: "explanation",
          audience: "both",
          tags: [],
          markdown: "",
          ...emptyIds(DOCUMENT_KINDS),
        },
  );
  const [value, setValue] = useState(initial);
  const [tags, setTags] = useState(initial.tags.join(", "));
  const [params] = useSearchParams();
  const [tab, setTab] = useState(() => {
    const wanted = params.get("tab") ?? "";
    return document && TABS.includes(wanted) ? wanted : "write";
  });
  // The URL may change after this editor mounted (Save draft and Promote set ?tab= while the editor reloads).
  const wantedTab = params.get("tab") ?? "";
  useEffect(() => {
    if (document && TABS.includes(wantedTab)) setTab(wantedTab);
  }, [wantedTab]);
  // A draft loaded with Edit: Save draft updates it instead of starting a new one.
  const [editingDraft, setEditingDraft] = useState<{ id: string; revision: number; title: string } | null>(null);
  const [historical, setHistorical] = useState<DocumentVersion | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  // An action from the Drafts tab (Edit, Promote) waiting for the confirmation that unsaved changes may be lost.
  const [unsavedAction, setUnsavedAction] = useState<{ run: () => void } | null>(null);
  const leaving = useRef(false);
  const [folder, setFolder] = useState(""); // the New page's agent only: not part of the document input
  useEffect(() => {
    onFieldsChange(value.title, folder);
  }, [value.title, folder]);
  useEffect(() => {
    registerLeave(() => {
      leaving.current = true;
    });
  }, []);
  const dirty = JSON.stringify(value) !== JSON.stringify(initial) || tags !== initial.tags.join(", ");
  const unlessUnsaved = (run: () => void) => {
    if (dirty) setUnsavedAction({ run });
    else run();
  };
  useEffect(() => {
    registerConfirm(unlessUnsaved);
  });
  useEffect(() => () => registerConfirm((run) => run()), []); // unmounted (reloading): nothing unsaved to ask about
  const patch = <K extends keyof DocumentInput>(key: K, next: DocumentInput[K]) =>
    setValue((old) => ({ ...old, [key]: next }));

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const saved = await saveDocument(
        {
          ...value,
          tags: tags
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean),
        },
        document?.id,
      );
      leaving.current = true;
      onSaved(saved.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save document");
    } finally {
      setPending(false);
    }
  }
  async function saveDraft() {
    setPending(true);
    setError("");
    const content = {
      title: value.title,
      description: value.description,
      purpose: value.purpose,
      audience: value.audience,
      tags: tags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
      markdown: value.markdown,
    };
    try {
      if (editingDraft) {
        const saved = await updateDraft(editingDraft.id, editingDraft.revision, content);
        leaving.current = true;
        onDraftSaved(saved.id);
      } else if (document) {
        const saved = await createDraft({ document_id: document.id, ...content });
        leaving.current = true;
        onDraftSaved(saved.id);
      } else {
        const saved = await createDraft({ slug: value.slug, ...content, resource_ids: pickIds(DOCUMENT_KINDS, value) });
        leaving.current = true;
        navigate(draftPath(saved));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the draft");
    } finally {
      setPending(false);
    }
  }
  async function openVersion(id: string) {
    if (!document) return;
    setPending(true);
    setError("");
    try {
      setHistorical(await getDocumentVersion(document.id, id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load version");
    } finally {
      setPending(false);
    }
  }
  async function remove() {
    if (!document) return;
    setPending(true);
    setError("");
    try {
      await deleteDocument(document.id, document.revision);
      leaving.current = true;
      navigate("/documents");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete document");
    } finally {
      setPending(false);
    }
  }
  return (
    <form onSubmit={save} className="flex flex-col gap-6">
      <UnsavedDocumentChanges dirty={dirty} leaving={leaving} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button asChild variant="ghost">
          <Link to="/documents">
            <ArrowLeft data-icon="inline-start" />
            Documents
          </Link>
        </Button>
        <div className="flex items-center gap-2">
          {document && (
            <Button
              type="button"
              variant="outline"
              disabled={pending || dirty}
              onClick={async () => {
                setPending(true);
                setError("");
                try {
                  const result = await exportDocument(document.id);
                  setNotice(`Exported to ${result.folder}`);
                } catch (e) {
                  setError(e instanceof Error ? e.message : "Could not export document");
                } finally {
                  setPending(false);
                }
              }}
            >
              Export to folder
            </Button>
          )}
          {document && (
            <Button asChild type="button" variant="outline">
              <a href={`/api/documentation/documents/${document.id}/bundle`}>Download document folder</a>
            </Button>
          )}
          {document && <Badge variant="secondary">Version {document.version}</Badge>}
          {document && (
            <Button
              type="button"
              variant="outline"
              disabled={pending}
              onClick={() => {
                setError("");
                setConfirmDelete(true);
              }}
            >
              <Trash2 data-icon="inline-start" />
              Delete
            </Button>
          )}
          {document && (
            <Button type="button" variant="outline" aria-pressed={agentOpen} onClick={onToggleAgent}>
              <Bot data-icon="inline-start" />
              Ask agent
            </Button>
          )}
          {!document && (
            <Button type="button" variant="outline" aria-pressed={agentOpen} onClick={onToggleAgent}>
              <Bot data-icon="inline-start" />
              Write with an agent
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            disabled={pending || !value.markdown.trim() || !value.title.trim()}
            onClick={() => void saveDraft()}
          >
            Save draft
          </Button>
          <Button type="submit" disabled={pending || !value.markdown.trim() || (!dirty && document !== null)}>
            {pending ? <Spinner /> : <Save data-icon="inline-start" />}
            {document ? "Save new version" : "Create document"}
          </Button>
        </div>
      </div>
      {error && !confirmDelete && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {notice && (
        <Alert role="status">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      )}
      {editingDraft && (
        <Alert role="status">
          <AlertDescription>{`Editing the draft “${editingDraft.title}”. Save draft updates it; Save new version makes this text the live version and leaves the draft as it is.`}</AlertDescription>
        </Alert>
      )}
      <fieldset disabled={pending} className="flex min-w-0 flex-col gap-6">
        <FieldGroup className="grid gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="doc-title">Title</FieldLabel>
            <Input
              id="doc-title"
              required
              maxLength={240}
              value={value.title}
              onChange={(e) => {
                const title = e.target.value;
                setValue((old) => ({
                  ...old,
                  title,
                  ...(!document &&
                  (old.slug === "" ||
                    old.slug ===
                      old.title
                        .toLowerCase()
                        .replace(/[^a-z0-9]+/g, "-")
                        .replace(/^-|-$/g, "")
                        .slice(0, 120))
                    ? {
                        slug: title
                          .toLowerCase()
                          .replace(/[^a-z0-9]+/g, "-")
                          .replace(/^-|-$/g, "")
                          .slice(0, 120),
                      }
                    : {}),
                }));
              }}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="doc-slug">Slug</FieldLabel>
            <Input
              id="doc-slug"
              required
              maxLength={120}
              pattern="[a-z0-9]+(-[a-z0-9]+)*"
              value={value.slug}
              onChange={(e) => patch("slug", e.target.value)}
            />
            <FieldDescription>Unique name using lowercase letters, numbers, and hyphens.</FieldDescription>
          </Field>
        </FieldGroup>
        {!document && agentOpen && (
          <Field>
            <FieldLabel htmlFor="doc-folder">Folder</FieldLabel>
            <Input
              id="doc-folder"
              maxLength={200}
              placeholder="payments"
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
            />
            <FieldDescription>
              {folder.trim() && folderError(folder.trim())
                ? folderError(folder.trim())
                : "The documentation folder the agent writes the document in."}
            </FieldDescription>
          </Field>
        )}
        <Field>
          <FieldLabel htmlFor="doc-description">Short description</FieldLabel>
          <Textarea
            id="doc-description"
            rows={2}
            maxLength={4000}
            value={value.description}
            onChange={(e) => patch("description", e.target.value)}
          />
        </Field>
        <FieldGroup className="grid gap-4 sm:grid-cols-3">
          <Field>
            <FieldLabel htmlFor="doc-purpose">Purpose</FieldLabel>
            <Input
              id="doc-purpose"
              list="document-purposes"
              required
              maxLength={80}
              value={value.purpose}
              onChange={(e) => patch("purpose", e.target.value)}
            />
            <datalist id="document-purposes">
              {["explanation", "onboarding", "architecture", "how-to", "reference", "runbook", "troubleshooting"].map(
                (p) => (
                  <option key={p} value={p} />
                ),
              )}
            </datalist>
          </Field>
          <Field>
            <FieldLabel htmlFor="doc-audience">Audience</FieldLabel>
            <Select value={value.audience} onValueChange={(v) => patch("audience", v as DocumentInput["audience"])}>
              <SelectTrigger id="doc-audience">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="both">People and agents</SelectItem>
                  <SelectItem value="people">People</SelectItem>
                  <SelectItem value="agents">Agents</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor="doc-tags">Tags</FieldLabel>
            <Input
              id="doc-tags"
              placeholder="Comma-separated tags"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
            />
          </Field>
        </FieldGroup>
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="write">Write</TabsTrigger>
            <TabsTrigger value="preview">Preview</TabsTrigger>
            <TabsTrigger value="resources">Linked resources</TabsTrigger>
            <TabsTrigger value="history" disabled={!document}>
              Version history
            </TabsTrigger>
            <TabsTrigger value="drafts" disabled={!document}>
              Drafts
            </TabsTrigger>
            <TabsTrigger value="connections" disabled={!document}>
              Connections
            </TabsTrigger>
            <TabsTrigger value="agent-runs" disabled={!document}>
              Agent runs
            </TabsTrigger>
          </TabsList>
          <TabsContent value="write" forceMount className="data-[state=inactive]:hidden">
            <Field>
              <FieldLabel htmlFor="doc-markdown">Markdown</FieldLabel>
              <Textarea
                id="doc-markdown"
                className="min-h-96 font-mono"
                required
                maxLength={1000000}
                value={value.markdown}
                onChange={(e) => patch("markdown", e.target.value)}
              />
              <FieldDescription>
                Every save preserves a complete version and updates the documents collection's folder; refresh the
                collection to update search.
              </FieldDescription>
            </Field>
          </TabsContent>
          <TabsContent value="preview">
            <div className="min-h-96 rounded-lg border p-4">
              {value.markdown ? (
                <Markdown content={value.markdown} />
              ) : (
                <p className="text-sm text-muted-foreground">Write Markdown to preview the document.</p>
              )}
            </div>
          </TabsContent>
          <TabsContent value="resources">
            <DocumentResourceSelection
              resources={resources}
              value={value}
              onChange={(next) =>
                setValue({
                  ...next,
                  source_commits: Object.fromEntries(
                    Object.entries(next.source_commits ?? {}).filter(([id]) => next.code_project_ids.includes(id)),
                  ),
                })
              }
            />
            {Object.keys(value.source_commits ?? {}).length > 0 && (
              <div className="mt-4 flex flex-col gap-2">
                <h3 className="font-medium">Documented source commits</h3>
                {Object.entries(value.source_commits ?? {}).map(([id, commit]) => (
                  <p key={id} className="text-sm">
                    {resources.code_projects.find((p) => p.id === id)?.name ?? id}:{" "}
                    <code className="break-all">{commit}</code>
                  </p>
                ))}
              </div>
            )}
          </TabsContent>
          <TabsContent value="history">
            <div className="grid gap-6 md:grid-cols-[240px_minmax(0,1fr)]">
              <div className="flex flex-col gap-2">
                {document?.versions.map((version) => (
                  <Button
                    key={version.id}
                    type="button"
                    variant={historical?.id === version.id ? "secondary" : "outline"}
                    className="h-auto justify-start whitespace-normal py-3 text-left"
                    onClick={() => void openVersion(version.id)}
                  >
                    <span>
                      Version {version.version}
                      {version.id === document.current_version_id ? " · Current" : ""}
                      {version.from_draft ? " · from draft" : ""}
                      <span className="block text-xs font-normal text-muted-foreground">
                        {new Date(version.created_at).toLocaleString()}
                      </span>
                    </span>
                  </Button>
                ))}
              </div>
              <div className="min-w-0 rounded-lg border p-4">
                {historical ? (
                  <>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <h2 className="font-semibold">
                        {historical.title} · v{historical.version}
                      </h2>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => {
                          setValue({
                            ...value,
                            title: historical.title,
                            description: historical.description,
                            purpose: historical.purpose,
                            audience: historical.audience,
                            tags: historical.tags,
                            markdown: historical.markdown,
                            source_commits: Object.fromEntries(
                              historical.source_snapshot.code_projects
                                .filter((r) => r.git_commit)
                                .map((r) => [r.id, r.git_commit!]),
                            ),
                            ...idsOf(DOCUMENT_KINDS, historical.source_snapshot),
                          });
                          setTags(historical.tags.join(", "));
                          setTab("write");
                          setNotice(
                            `Version ${historical.version} loaded into the editor. Save to create a new version.`,
                          );
                        }}
                      >
                        Use version as draft
                      </Button>
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">
                      {historical.purpose} · {historical.audience} · {historical.origin}
                    </p>
                    <p className="mt-2 text-sm text-muted-foreground">
                      Resources at save time:{" "}
                      {Object.values(historical.source_snapshot)
                        .flat()
                        .map((r) => r.name)
                        .join(", ") || "None"}
                    </p>
                    {historical.source_snapshot.code_projects
                      .filter((r) => r.git_commit)
                      .map((r) => (
                        <p key={r.id} className="mt-2 text-sm">
                          {r.name}: <code className="break-all">{r.git_commit}</code>
                        </p>
                      ))}
                    <Markdown content={historical.markdown} />
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Select a version to read it or use it as the starting point for an edit.
                  </p>
                )}
              </div>
            </div>
          </TabsContent>
          <TabsContent value="drafts">
            {document && (
              <DocumentDrafts
                documentId={document.id}
                selected={params.get("draft")}
                onPromoted={onPromoted}
                confirm={unlessUnsaved}
                onEdit={(draft) => {
                  setValue((old) => ({
                    ...old,
                    title: draft.title,
                    description: draft.description,
                    purpose: draft.purpose,
                    audience: draft.audience,
                    tags: draft.tags,
                    markdown: draft.markdown,
                  }));
                  setTags(draft.tags.join(", "));
                  setEditingDraft({ id: draft.id, revision: draft.revision, title: draft.title });
                  setTab("write");
                  setNotice(`Draft “${draft.title}” loaded into the editor.`);
                }}
              />
            )}
          </TabsContent>
          <TabsContent value="connections">{document && <DocumentConnections documentId={document.id} />}</TabsContent>
          <TabsContent value="agent-runs">{document && <DocumentAgentRuns documentId={document.id} />}</TabsContent>
        </Tabs>
      </fieldset>
      <AlertDialog
        open={unsavedAction !== null}
        onOpenChange={(open) => {
          if (!open) setUnsavedAction(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>
              The editor has changes that are not saved. Continuing replaces them; they will be lost.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                const action = unsavedAction;
                setUnsavedAction(null);
                action?.run();
              }}
            >
              Discard changes
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog
        open={confirmDelete}
        onOpenChange={(open) => {
          if (!pending) setConfirmDelete(open);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{document?.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the document, all saved versions, and its resource links. The linked code projects, services,
              and servers remain. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={pending}
              onClick={(e) => {
                e.preventDefault();
                void remove();
              }}
            >
              {pending && <Spinner />}Delete document
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </form>
  );
}

export default function DocumentEditorView() {
  const { documentId, runId } = useParams();
  const navigate = useNavigate();
  const [, setParams] = useSearchParams();
  const [loaded, setLoaded] = useState<{ document: DocumentDetail | null; resources: DocumentResources } | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  // The New page's agent pane starts closed and is not remembered (Ask agent's choice is per document page).
  const [agentOpen, setAgentOpen] = useState(
    () => documentId !== undefined && (runId !== undefined || readPane() === "open"),
  );
  const [agentDraft, setAgentDraft] = useState<SessionDetail["document_draft"] | null>(null);
  const [agentVersion, setAgentVersion] = useState(""); // changes after each of the agent's turns: the bar reloads the draft
  const confirmRef = useRef<(run: () => void) => void>((run) => run());
  const leaveRef = useRef<() => void>(() => {});
  const [fields, setFields] = useState({ title: "", folder: "" }); // the New page's Title and Folder (the agent writes them)
  // Loads only while the pane is open; a new document's options once per page, whatever the title.
  const settings = useAgentSettings(
    !agentOpen
      ? null
      : documentId
        ? { kind: "document", id: documentId, title: "" }
        : { kind: "new-document", ...fields },
  );
  // A notice handed over in the navigation state (the new-document draft page's Promote, with a failed export).
  const location = useLocation();
  const handed = (location.state as { notice?: unknown } | null)?.notice;
  const [notice, setNotice] = useState(typeof handed === "string" ? handed : "");
  useEffect(() => {
    const controller = new AbortController();
    setLoaded(null);
    setError("");
    Promise.all([
      documentId ? getDocument(documentId, controller.signal) : Promise.resolve(null),
      getDocumentResources(controller.signal),
    ])
      .then(([document, resources]) => {
        if (!controller.signal.aborted) setLoaded({ document, resources });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not open document");
      });
    return () => controller.abort();
  }, [documentId, refresh]);
  // A run link followed on this page (the Agent runs tab, a draft's Agent run) opens the pane on that run.
  useEffect(() => {
    if (runId !== undefined) setAgentOpen(true);
  }, [runId]);
  // The URL follows the pane's run (a reload reopens it), keeping ?tab= and ?draft=.
  const followRun = (id: string | null) => {
    if (!documentId) return;
    const pathname = id ? agentRunPath(documentId, id) : `/documents/${encodeURIComponent(documentId)}`;
    if (pathname !== location.pathname) navigate({ pathname, search: location.search }, { replace: true });
  };
  const toggleAgent = () => {
    const next = !agentOpen;
    setAgentOpen(next);
    if (documentId) write(PANE_KEY, next ? "open" : "closed");
    if (!next) {
      setAgentDraft(null);
      if (runId !== undefined) followRun(null);
    } // a closed pane keeps no run in the URL
  };
  const onPromoted = (result: PromoteResult) => {
    setNotice(promotedNotice(result));
    setAgentDraft((d) => (d && d.id === result.draft.id ? { ...d, status: result.draft.status } : d));
    setParams({ tab: "drafts" }, { replace: true });
    setRefresh((n) => n + 1);
  };
  const editor = (current: { document: DocumentDetail | null; resources: DocumentResources }) => (
    <Editor
      key={`${documentId}-${refresh}`}
      {...current}
      onSaved={(id) => {
        setNotice("Document version saved.");
        if (documentId === id) setRefresh((n) => n + 1);
        else navigate(`/documents/${id}`);
      }}
      onDraftSaved={(draftId) => {
        setNotice("Draft saved.");
        setParams({ tab: "drafts", draft: draftId }, { replace: true });
        setRefresh((n) => n + 1);
      }}
      onPromoted={onPromoted}
      agentOpen={agentOpen}
      onToggleAgent={toggleAgent}
      registerConfirm={(confirm) => {
        confirmRef.current = confirm;
      }}
      onFieldsChange={(title, folder) =>
        setFields((old) => (old.title === title && old.folder === folder ? old : { title, folder }))
      }
      registerLeave={(leave) => {
        leaveRef.current = leave;
      }}
    />
  );
  // The pane lives outside the editor and is rendered whenever it is open, so it keeps its conversation across the
  // editor's reload (refresh) and remounts only when the document changes.
  const [titled, setTitled] = useState<{ id: string; title: string } | null>(null);
  useEffect(() => {
    if (loaded?.document) setTitled({ id: loaded.document.id, title: loaded.document.title });
  }, [loaded]);
  const paneTitle = agentOpen && documentId && titled?.id === documentId ? titled.title : null;
  const writing = agentOpen && !documentId && loaded !== null; // the New page's Write with an agent
  const split = paneTitle !== null || writing;
  // With the pane open the page takes the full width: the document 2/3, the chat 1/3 (below the editor on small screens).
  useWidePage(split);
  // A new document's promoted draft is the document: open it (the editor's fields were the agent's input).
  const onNewPromoted = (result: PromoteResult) => {
    leaveRef.current();
    navigate(`/documents/${encodeURIComponent(result.document_id)}`, { state: { notice: promotedNotice(result) } });
  };
  return (
    <>
      {notice && (
        <Alert role="status">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      )}
      <div
        data-testid="document-split"
        className={split ? "grid min-w-0 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:items-start" : "min-w-0"}
      >
        <div className="min-w-0">
          {paneTitle !== null && agentDraft && (
            <AgentDraftBar
              draft={agentDraft}
              version={agentVersion}
              confirm={(run) => confirmRef.current(run)}
              onPromoted={onPromoted}
            />
          )}
          {writing && agentDraft && (
            <AgentDraftBar
              draft={agentDraft}
              version={agentVersion}
              confirm={(run) => run()}
              onPromoted={onNewPromoted}
              onCompare={(d) => navigate(draftPath(d))}
            />
          )}
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>
                {error}{" "}
                <Button variant="link" onClick={() => setRefresh((n) => n + 1)}>
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          ) : loaded ? (
            editor(loaded)
          ) : (
            <Skeleton className="h-96 w-full" />
          )}
        </div>
        {paneTitle !== null && (
          <div className="min-w-0 lg:sticky lg:top-4">
            <AgentChat
              key={documentId}
              scope={{ kind: "document", id: documentId!, title: paneTitle }}
              settings={settings}
              initialRunId={runId ?? null}
              confirmUnsaved={(run) => confirmRef.current(run)}
              onDraftChange={(draft, version) => {
                setAgentDraft(draft);
                setAgentVersion(version);
              }}
              onRunChange={followRun}
            />
          </div>
        )}
        {writing && (
          <div className="min-w-0 lg:sticky lg:top-4">
            <AgentChat
              key="new"
              scope={{ kind: "new-document", ...fields }}
              settings={settings}
              initialRunId={null}
              onDraftChange={(draft, version) => {
                setAgentDraft(draft);
                setAgentVersion(version);
              }}
            />
          </div>
        )}
      </div>
    </>
  );
}
