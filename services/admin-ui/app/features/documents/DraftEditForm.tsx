import { useState } from "react";
import { updateDraft, type DraftDetail } from "./drafts";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

/** Edit a new document's draft on its page: title, description and Markdown; Save draft keeps its other fields. */
export default function DraftEditForm({
  draft,
  onCancel,
  onSaved,
}: {
  draft: DraftDetail;
  onCancel: () => void;
  onSaved: (draft: DraftDetail) => void;
}) {
  const [title, setTitle] = useState(draft.title);
  const [description, setDescription] = useState(draft.description);
  const [markdown, setMarkdown] = useState(draft.markdown);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    setError("");
    try {
      onSaved(
        await updateDraft(draft.id, draft.revision, {
          title,
          description,
          purpose: draft.purpose,
          audience: draft.audience,
          tags: draft.tags,
          markdown,
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the draft");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="Edit draft" className="flex flex-col gap-4">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Field>
        <FieldLabel htmlFor="draft-title">Title</FieldLabel>
        <Input id="draft-title" maxLength={240} value={title} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field>
        <FieldLabel htmlFor="draft-description">Short description</FieldLabel>
        <Textarea
          id="draft-description"
          rows={2}
          maxLength={4000}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </Field>
      <Field>
        <FieldLabel htmlFor="draft-markdown">Markdown</FieldLabel>
        <Textarea
          id="draft-markdown"
          className="min-h-96 font-mono"
          maxLength={1000000}
          value={markdown}
          onChange={(e) => setMarkdown(e.target.value)}
        />
      </Field>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="button" disabled={busy || !title.trim() || !markdown.trim()} onClick={() => void save()}>
          {busy && <Spinner data-icon="inline-start" />}Save draft
        </Button>
      </div>
    </section>
  );
}
