import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import BackLink from "~/components/BackLink";
import ConfirmRemove from "~/components/ConfirmRemove";
import LeaveGuard from "~/components/LeaveGuard";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import { Bot, Save, Trash2 } from "lucide-react";
import {
  deleteDocument,
  getDocument,
  getDocumentResources,
  getDocumentVersion,
  isBuiltForRefusal,
  newDocumentResource,
  saveDocumentLinks,
  type DocumentDetail,
  type DocumentIds,
  type DocumentInput,
  type DocumentResources,
  type DocumentVersion,
} from "./api";
import { ApiError } from "~/lib/api";
import { agentRunPath } from "~/features/sessions/api";
import { useAgentSettings } from "~/features/sessions/agentSettings";
import { useWidePage } from "~/lib/widePage";
import type { SessionDetail } from "~/features/sessions/api";
import {
  createDraft,
  discardDraft,
  getDraft,
  promoteDraft,
  promotedNotice,
  publishedNotice,
  updateDraft,
  type PromoteResult,
} from "./drafts";
import AgentDraftBar from "./AgentDraftBar";
import AgentChat from "~/features/sessions/AgentChat";
import DocumentAgentRuns from "./DocumentAgentRuns";
import DocumentConnections from "./DocumentConnections";
import DocumentDrafts from "./DocumentDrafts";
import DocumentLinkHistory from "./DocumentLinkHistory";
import Markdown from "~/components/Markdown";
import DocumentResourceSelection from "./DocumentResourceSelection";
import TagLinks from "./TagLinks";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel, FieldDescription, FieldError } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Textarea } from "~/components/ui/textarea";
import { DOCUMENT_KINDS, emptyIds, pickIds } from "~/lib/resources";

// The document page with its Ask agent run (/documents/:id/agent/:runId) is the same page: the editor stays.
const documentPage = (pathname: string) => pathname.replace(/\/agent\/[^/]+\/?$/, "");
const samePage = (a: string, b: string) => documentPage(a) === documentPage(b);

const TABS = ["write", "preview", "resources", "history", "drafts", "connections", "agent-runs"];
/** A brand-new draft's tabs: no versions, drafts or connections until it is published. */
const DRAFT_TABS = ["write", "preview", "resources", "agent-runs"];

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

/** The editor's content: what Save writes into a draft (a brand-new draft's name too). */
interface Content {
  slug: string;
  title: string;
  description: string;
  purpose: string;
  audience: DocumentInput["audience"];
  tags: string;
  markdown: string;
}
const splitTags = (tags: string) =>
  tags
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
/** The same links, whatever their order. */
const sameIds = (a: DocumentIds, b: DocumentIds) =>
  DOCUMENT_KINDS.every(({ field }) => {
    const left = [...(a[field] ?? [])].sort();
    const right = [...(b[field] ?? [])].sort();
    return left.length === right.length && left.every((id, i) => id === right[i]);
  });
/** The draft a person is editing on a live document's page: Save updates it (its id and revision). */
interface EditingDraft {
  id: string;
  revision: number;
  created_at: string;
  base_version: number | null;
}

/** The editor, in three modes. The New page (`document` null): Save creates a brand-new draft and opens it. A brand-new
 * draft (`document.is_draft`): Save updates it (its name too), Publish makes it the live document, Discard deletes it.
 * A live document: the first Save creates the person's draft and the editor switches to it (later Saves update it);
 * Publish on the Drafts tab makes a draft live. Links are saved apart, with Save links. */
function Editor({
  document,
  resources,
  onCreated,
  onPublished,
  onPromoted,
  agentOpen,
  onToggleAgent,
  registerConfirm,
}: {
  document: DocumentDetail | null;
  resources: DocumentResources;
  onCreated: (id: string) => void;
  onPublished: (result: PromoteResult) => void;
  onPromoted: (result: PromoteResult) => void;
  agentOpen: boolean;
  onToggleAgent: () => void;
  registerConfirm: (confirm: (run: () => void) => void) => void;
}) {
  const navigate = useNavigate();
  const isDraft = document?.is_draft === true; // a brand-new draft: not published yet
  const isLive = document !== null && !isDraft;
  const [params] = useSearchParams();
  // The New page opened by a resource's New document (?resource=server&resource_id=…): built for that resource.
  const [resourceFor] = useState(() => {
    const found = document ? null : newDocumentResource(params);
    if (!found) return null;
    const list = found.type === "code_project" ? resources.code_projects : resources[`${found.type}s`];
    return { ...found, name: list?.find((item) => item.id === found.id)?.name ?? found.id };
  });
  const [initial] = useState<DocumentInput>(() =>
    document
      ? {
          slug: document.slug,
          title: document.title,
          description: document.description,
          purpose: document.purpose,
          audience: document.audience,
          tags: document.tags,
          markdown: document.markdown,
          ...pickIds(DOCUMENT_KINDS, document),
          source_commits: Object.fromEntries(
            (document.source_snapshot?.code_projects ?? [])
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
          ...(resourceFor ? { [`${resourceFor.type}_ids`]: [resourceFor.id] } : {}),
        },
  );
  const [value, setValue] = useState(initial);
  const [tags, setTags] = useState(initial.tags.join(", "));
  const content = (): Content => ({
    slug: value.slug,
    title: value.title,
    description: value.description,
    purpose: value.purpose,
    audience: value.audience,
    tags,
    markdown: value.markdown,
  });
  // What the last Save (or the load) left: the editor is dirty when its content or links differ from it.
  const [savedContent, setSavedContent] = useState<Content>(content);
  /** The live document's content as the page loaded it: the editor's baseline when it edits no draft. */
  const loadedContent = (): Content => ({
    slug: initial.slug,
    title: initial.title,
    description: initial.description,
    purpose: initial.purpose,
    audience: initial.audience,
    tags: initial.tags.join(", "),
    markdown: initial.markdown,
  });
  const [savedIds, setSavedIds] = useState<DocumentIds>(() => pickIds(DOCUMENT_KINDS, initial));
  // The document's revision (a brand-new draft's is the draft's): Save links and Delete send it, a brand-new draft's
  // Save too; Save links bumps it.
  const [revision, setRevision] = useState(document?.revision ?? 0);
  const tabs = isDraft ? DRAFT_TABS : TABS;
  const [tab, setTab] = useState(() => {
    const wanted = params.get("tab") ?? "";
    return document && tabs.includes(wanted) ? wanted : "write";
  });
  // The URL may change after this editor mounted (Publish sets ?tab= while the editor reloads).
  const wantedTab = params.get("tab") ?? "";
  useEffect(() => {
    if (document && tabs.includes(wantedTab)) setTab(wantedTab);
  }, [wantedTab]);
  // The person's draft of the live document: created by the first Save (or loaded with Edit); later Saves update it.
  const [editingDraft, setEditingDraft] = useState<EditingDraft | null>(null);
  const [draftCount, setDraftCount] = useState(document?.drafts.length ?? 0);
  // The live version the page loaded (reloaded when the edited draft is gone): the first Save's draft is based on it,
  // so a version promoted meanwhile makes that draft stale.
  const [liveVersion, setLiveVersion] = useState(isLive ? document.version : null);
  const [historical, setHistorical] = useState<DocumentVersion | null>(null);
  // The document's active links: the built-for ones are locked in Linked resources. Save links refused because it
  // unticked a built-for link (the page was loaded before that link existed) reloads them; refusals show under the
  // picker.
  const [links, setLinks] = useState(document?.resource_links ?? []);
  const [linksError, setLinksError] = useState("");
  const [linksLoad, setLinksLoad] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  // The New page's name refused (a live document or a brand-new draft holds it): shown under Slug.
  const [nameError, setNameError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  // An action from the Drafts tab (Edit, Publish) waiting for the confirmation that unsaved changes may be lost.
  // An in-page action that would replace unsaved text (Edit or Publish a draft, the agent's first message) asks
  // "Discard unsaved changes?" here; leaving the page is asked by LeaveGuard (Stay / Leave).
  const [unsavedAction, setUnsavedAction] = useState<{ run: () => void } | null>(null);
  const leaving = useRef(false);
  const ids = pickIds(DOCUMENT_KINDS, value);
  const contentDirty = JSON.stringify(content()) !== JSON.stringify(savedContent);
  const linksDirty = !sameIds(ids, savedIds);
  const dirty = contentDirty || linksDirty;
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
  const failed = (e: unknown, fallback: string) => setError(e instanceof Error ? e.message : fallback);

  /** The draft being edited is gone (published or discarded elsewhere): edit no draft, keep the person's text, reload the
   * document's version, revision and drafts count; the next Save starts a new draft from that version. */
  async function draftGone(documentId: string) {
    setEditingDraft(null);
    setSavedContent(loadedContent());
    let version = liveVersion;
    try {
      const fresh = await getDocument(documentId);
      version = fresh.version;
      setLiveVersion(fresh.version);
      setRevision(fresh.revision);
      setDraftCount(fresh.drafts.length);
    } catch {
      /* the message still shows; a page reload loads the document */
    }
    setError(`Your draft was published or discarded elsewhere. Save starts a new draft from version ${version}.`);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    setNameError("");
    const now = content();
    const text = {
      title: value.title,
      description: value.description,
      purpose: value.purpose,
      audience: value.audience,
      tags: splitTags(tags),
      markdown: value.markdown,
    };
    try {
      if (!document) {
        const saved = await createDraft({ slug: value.slug, ...text, resource_ids: ids });
        leaving.current = true;
        onCreated(saved.id);
        return;
      }
      if (isDraft) {
        const saved = await updateDraft(document.id, revision, text, value.slug);
        setRevision(saved.revision);
      } else if (editingDraft) {
        const saved = await updateDraft(editingDraft.id, editingDraft.revision, text).catch((e) => {
          if (e instanceof ApiError && e.status === 404) return null;
          throw e;
        });
        if (!saved) {
          await draftGone(document.id);
          return;
        }
        setEditingDraft({ ...editingDraft, revision: saved.revision });
      } else {
        const saved = await createDraft({
          document_id: document.id,
          ...(liveVersion !== null ? { base_version: liveVersion } : {}),
          ...text,
        });
        setEditingDraft({
          id: saved.id,
          revision: saved.revision,
          created_at: saved.created_at,
          base_version: saved.base_version,
        });
        setDraftCount((n) => n + 1);
      }
      setSavedContent(now);
      toast.success("Draft saved.");
    } catch (e) {
      if (!document && e instanceof ApiError && e.status === 409) setNameError(e.message);
      else failed(e, "Could not save the draft");
    } finally {
      setPending(false);
    }
  }
  /** The picker's links, with every built-for link kept (it is locked; a loaded version cannot drop it). */
  function keptIds(): DocumentIds {
    return Object.fromEntries(
      DOCUMENT_KINDS.map(({ field }) => [
        field,
        [
          ...new Set([
            ...(value[field] ?? []),
            ...links
              .filter((link) => link.connection === "built_for" && `${link.type}_ids` === field)
              .map((link) => link.id),
          ]),
        ],
      ]),
    ) as DocumentIds;
  }
  async function saveLinks() {
    if (!document) return;
    setPending(true);
    setLinksError("");
    try {
      const kept = keptIds();
      const saved = await saveDocumentLinks(document.id, revision, kept);
      setRevision(saved.revision);
      setLinks(saved.resource_links);
      setValue((old) => ({ ...old, ...kept }));
      setSavedIds(kept);
      setLinksLoad((n) => n + 1);
    } catch (e) {
      setLinksError(e instanceof Error ? e.message : "Could not save the links");
      if (isBuiltForRefusal(e)) await reloadLinks(document.id);
    } finally {
      setPending(false);
    }
  }
  /** The document's current links into the picker (built-for ones locked, ticked); the text stays. A live document
   * takes the fresh revision too (it only guards links and Delete); a brand-new draft keeps its own (it guards the
   * text). */
  async function reloadLinks(id: string) {
    try {
      const fresh = await getDocument(id);
      const freshIds = pickIds(DOCUMENT_KINDS, fresh);
      setLinks(fresh.resource_links);
      setValue((old) => ({ ...old, ...freshIds }));
      setSavedIds(freshIds);
      if (!fresh.is_draft) setRevision(fresh.revision);
      setLinksLoad((n) => n + 1);
    } catch {
      /* the refusal stays shown; a page reload loads the links */
    }
  }
  async function openVersion(number: number) {
    if (!document) return;
    setPending(true);
    setError("");
    try {
      setHistorical(await getDocumentVersion(document.id, number));
    } catch (e) {
      failed(e, "Could not load version");
    } finally {
      setPending(false);
    }
  }
  async function remove() {
    if (!document) return;
    setError("");
    await deleteDocument(document.id, revision); // a failure stays in the dialog
    leaving.current = true;
    navigate("/documents");
  }
  async function publish() {
    if (!document) return;
    setPending(true);
    setError("");
    try {
      const result = await promoteDraft(document.id, revision, false);
      leaving.current = true;
      onPublished(result);
    } catch (e) {
      failed(e, "Could not publish the draft");
      // A live document took the name meanwhile: the draft now revises it and opens on the draft page, with why.
      if (e instanceof ApiError && e.status === 409) {
        const now = await getDraft(document.id).catch(() => null);
        if (now?.document_id) {
          leaving.current = true;
          navigate(`/documents/drafts/${encodeURIComponent(now.id)}`, { state: { notice: e.message } });
        }
      }
    } finally {
      setPending(false);
    }
  }
  async function discard() {
    if (!document) return;
    setPending(true);
    setError("");
    try {
      await discardDraft(document.id, revision);
      setConfirmDiscard(false);
      leaving.current = true;
      navigate("/documents");
    } catch (e) {
      setConfirmDiscard(false);
      failed(e, "Could not discard the draft");
    } finally {
      setPending(false);
    }
  }
  return (
    <form onSubmit={save} className="flex flex-col gap-6">
      <LeaveGuard dirty={dirty} samePage={samePage} leaving={leaving} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <BackLink to="/documents">All documents</BackLink>
        <div className="flex items-center gap-2">
          {isLive && (
            <Button asChild type="button" variant="outline">
              <a href={`/api/documentation/documents/${document.id}/bundle`}>Download document</a>
            </Button>
          )}
          {isLive && <Badge variant="secondary">Version {liveVersion}</Badge>}
          {isLive && (
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
          {isLive && (
            <Button type="button" variant="outline" aria-pressed={agentOpen} onClick={onToggleAgent}>
              <Bot data-icon="inline-start" />
              Ask agent
            </Button>
          )}
          {isDraft && (
            <Button
              type="button"
              variant="outline"
              disabled={pending}
              onClick={() => {
                setError("");
                setConfirmDiscard(true);
              }}
            >
              Discard
            </Button>
          )}
          {isDraft && (
            <Button
              type="button"
              variant="outline"
              disabled={pending || dirty}
              title={dirty ? "Save your changes first." : undefined}
              onClick={() => void publish()}
            >
              Publish
            </Button>
          )}
          <Button
            type="submit"
            disabled={pending || !value.markdown.trim() || !value.title.trim() || (document !== null && !contentDirty)}
          >
            {pending ? <Spinner /> : <Save data-icon="inline-start" />}
            Save
          </Button>
        </div>
      </div>
      {error && !confirmDelete && !confirmDiscard && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {isDraft && (
        <Alert role="status">
          <AlertDescription>Draft — not published yet</AlertDescription>
        </Alert>
      )}
      {resourceFor && (
        <Alert role="status">
          <AlertDescription>{`New document for ${resourceFor.type.replace("_", " ")} ${resourceFor.name}.`}</AlertDescription>
        </Alert>
      )}
      {editingDraft && (
        <Alert role="status">
          <AlertDescription>{`Editing your draft (created ${new Date(editingDraft.created_at).toLocaleString()}, from version ${editingDraft.base_version ?? "?"}). Save updates it; Publish makes it live.`}</AlertDescription>
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
                setNameError(""); // the name may follow the title
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
              readOnly={isLive}
              maxLength={120}
              pattern="[a-z0-9]+(-[a-z0-9]+)*"
              value={value.slug}
              aria-invalid={nameError ? true : undefined}
              onChange={(e) => {
                setNameError("");
                patch("slug", e.target.value);
              }}
            />
            <FieldDescription>
              {isLive
                ? "A published document keeps its name."
                : "Unique name using lowercase letters, numbers, and hyphens."}
            </FieldDescription>
            {nameError && <FieldError>{nameError}</FieldError>}
          </Field>
        </FieldGroup>
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
            {/* the saved tags, each a link to the Documents list filtered by it */}
            <TagLinks tags={splitTags(savedContent.tags)} label="Document tags" />
          </Field>
        </FieldGroup>
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="write">Write</TabsTrigger>
            <TabsTrigger value="preview">Preview</TabsTrigger>
            <TabsTrigger value="resources">Linked resources</TabsTrigger>
            {!isDraft && (
              <>
                <TabsTrigger value="history" disabled={!document}>
                  Version history
                </TabsTrigger>
                <TabsTrigger value="drafts" disabled={!document}>
                  Drafts
                  {draftCount > 0 && (
                    <Badge variant="secondary" className="ml-1">
                      {draftCount}
                    </Badge>
                  )}
                </TabsTrigger>
                <TabsTrigger value="connections" disabled={!document}>
                  Connections
                </TabsTrigger>
              </>
            )}
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
                {isLive
                  ? "Save keeps your changes as your draft of this document; Publish on the Drafts tab makes a draft the live version."
                  : "Save keeps the document as a draft until it is published."}
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
              builtFor={resourceFor ? [resourceFor] : links.filter((link) => link.connection === "built_for")}
              onChange={(next) =>
                setValue({
                  ...next,
                  source_commits: Object.fromEntries(
                    Object.entries(next.source_commits ?? {}).filter(([id]) => next.code_project_ids.includes(id)),
                  ),
                })
              }
            />
            {document && (
              <div className="mt-4 flex justify-end">
                <Button type="button" disabled={pending || !linksDirty} onClick={() => void saveLinks()}>
                  Save links
                </Button>
              </div>
            )}
            {linksError && (
              <Alert variant="destructive" className="mt-4">
                <AlertDescription>{linksError}</AlertDescription>
              </Alert>
            )}
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
            {document && <DocumentLinkHistory documentId={document.id} reloadKey={linksLoad} />}
          </TabsContent>
          {isLive && (
            <TabsContent value="history">
              <div className="grid gap-6 md:grid-cols-[240px_minmax(0,1fr)]">
                <div className="flex flex-col gap-2">
                  {document.versions.map((version) => (
                    <Button
                      key={version.version}
                      type="button"
                      variant={historical?.version === version.version ? "secondary" : "outline"}
                      className="h-auto justify-start whitespace-normal py-3 text-left"
                      onClick={() => void openVersion(version.version)}
                    >
                      <span>
                        Version {version.version}
                        {version.live ? " · Current" : ""}
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
                            // The version's text only: links are saved apart (Save links).
                            setValue({
                              ...value,
                              title: historical.title,
                              description: historical.description,
                              purpose: historical.purpose,
                              audience: historical.audience,
                              tags: historical.tags,
                              markdown: historical.markdown,
                            });
                            setTags(historical.tags.join(", "));
                            setTab("write");
                            toast.success(
                              `Version ${historical.version} loaded into the editor. Save keeps it as your draft.`,
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
          )}
          {isLive && (
            <TabsContent value="drafts">
              <DocumentDrafts
                documentId={document.id}
                selected={params.get("draft")}
                onPromoted={onPromoted}
                confirm={unlessUnsaved}
                onCount={setDraftCount}
                onDiscarded={(id) => {
                  // The edited draft is gone: Save starts a new draft from the editor's text.
                  if (editingDraft?.id === id) {
                    setEditingDraft(null);
                    setSavedContent(loadedContent());
                  }
                }}
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
                  setSavedContent({
                    ...content(),
                    title: draft.title,
                    description: draft.description,
                    purpose: draft.purpose,
                    audience: draft.audience,
                    tags: draft.tags.join(", "),
                    markdown: draft.markdown,
                  });
                  setEditingDraft({
                    id: draft.id,
                    revision: draft.revision,
                    created_at: draft.created_at,
                    base_version: draft.base_version,
                  });
                  setTab("write");
                  toast.success(`Draft “${draft.title}” loaded into the editor.`);
                }}
              />
            </TabsContent>
          )}
          {isLive && (
            <TabsContent value="connections">
              <DocumentConnections documentId={document.id} />
            </TabsContent>
          )}
          <TabsContent value="agent-runs">{document && <DocumentAgentRuns documentId={document.id} />}</TabsContent>
        </Tabs>
      </fieldset>
      <ConfirmRemove
        removal={
          unsavedAction && {
            title: "Discard unsaved changes?",
            description: "The editor has changes that are not saved. Continuing replaces them; they will be lost.",
            action: "Discard changes",
            run: unsavedAction.run,
          }
        }
        onClose={() => setUnsavedAction(null)}
      />
      <ConfirmRemove
        removal={
          confirmDiscard
            ? {
                title: `Discard “${document?.title ?? ""}”?`,
                description: "The draft is deleted (the audit history keeps a record). No document is created.",
                action: "Discard draft",
                run: discard,
              }
            : null
        }
        onClose={() => setConfirmDiscard(false)}
        busy={pending}
      />
      <ConfirmRemove
        removal={
          confirmDelete
            ? {
                title: `Delete “${document?.title ?? ""}”?`,
                description:
                  "This removes the document, all saved versions, and its resource links. The linked code projects, services, and servers remain. This cannot be undone.",
                action: "Delete document",
                run: remove,
              }
            : null
        }
        onClose={() => setConfirmDelete(false)}
      />
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
  // Ask agent's pane: a live document's (the New page writes with an agent on its own sub-page), remembered per browser.
  const [agentOpen, setAgentOpen] = useState(
    () => documentId !== undefined && (runId !== undefined || readPane() === "open"),
  );
  const [agentDraft, setAgentDraft] = useState<SessionDetail["document_draft"] | null>(null);
  const [agentVersion, setAgentVersion] = useState(""); // changes after each of the agent's turns: the bar reloads the draft
  const confirmRef = useRef<(run: () => void) => void>((run) => run());
  // Loads only while the pane is open.
  const settings = useAgentSettings(agentOpen && documentId ? { kind: "document", id: documentId, title: "" } : null);
  const location = useLocation();
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
      .catch(async (e) => {
        // A draft of a live document is no document (404 here): it opens on the draft page.
        if (documentId && e instanceof ApiError && e.status === 404) {
          const draft = await getDraft(documentId, controller.signal).catch(() => null);
          if (controller.signal.aborted) return;
          if (draft?.document_id) {
            navigate(`/documents/drafts/${encodeURIComponent(draft.id)}`, { replace: true });
            return;
          }
        }
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
    toast.success(promotedNotice(result));
    // The promote deleted every draft of the document: the pane's run's draft was promoted or replaced.
    setAgentDraft((d) =>
      d && d.id === result.draft_id
        ? { ...d, status: "promoted" }
        : d && d.status === "open" && d.document_id === result.document_id
          ? { ...d, status: "replaced" }
          : d,
    );
    setParams({ tab: "drafts" }, { replace: true });
    setRefresh((n) => n + 1);
  };
  const editor = (current: { document: DocumentDetail | null; resources: DocumentResources }) => (
    <Editor
      key={`${documentId}-${refresh}`}
      {...current}
      onCreated={(id) => navigate(`/documents/${encodeURIComponent(id)}`)}
      onPublished={(result) => {
        toast.success(publishedNotice(result));
        setRefresh((n) => n + 1);
      }}
      onPromoted={onPromoted}
      agentOpen={agentOpen}
      onToggleAgent={toggleAgent}
      registerConfirm={(confirm) => {
        confirmRef.current = confirm;
      }}
    />
  );
  // The pane lives outside the editor and is rendered whenever it is open, so it keeps its conversation across the
  // editor's reload (refresh) and remounts only when the document changes.
  const [titled, setTitled] = useState<{ id: string; title: string } | null>(null);
  useEffect(() => {
    if (loaded?.document) setTitled({ id: loaded.document.id, title: loaded.document.title });
  }, [loaded]);
  // A brand-new draft has no Ask agent (its runs are the New page's): the pane is a live document's.
  const paneTitle =
    agentOpen && documentId && titled?.id === documentId && loaded?.document?.is_draft !== true ? titled.title : null;
  const split = paneTitle !== null;
  // With the pane open the page takes the full width: the document 2/3, the chat 1/3 (below the editor on small screens).
  useWidePage(split);
  return (
    <>
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
      </div>
    </>
  );
}
