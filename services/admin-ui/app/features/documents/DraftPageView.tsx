import { useState } from "react";
import BackLink from "~/components/BackLink";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { promotedNotice, type DraftDetail } from "./drafts";
import DraftEditForm from "./DraftEditForm";
import DraftView from "./DraftView";
import { Alert, AlertDescription } from "~/components/ui/alert";

/** /documents/drafts/:draftId — a draft of a live document on its own page (the environment's Drafts to review links here): the
 * changes against the live version, sources, Edit, Discard and Promote, which opens the document. A brand-new draft
 * opens at /documents/{id} (the id the document keeps when it is published): this page sends it there. */
export default function DraftPageView() {
  const { draftId } = useParams();
  const navigate = useNavigate();
  const [editing, setEditing] = useState<DraftDetail | null>(null);
  const [reload, setReload] = useState(0);
  // A notice handed over in the navigation state (a Publish refused because the draft now revises a document).
  const handed = (useLocation().state as { notice?: unknown } | null)?.notice;
  const [notice, setNotice] = useState(typeof handed === "string" ? handed : "");
  const [draft, setDraft] = useState<DraftDetail | null>(null);
  const documentPath = draft?.document_id ? `/documents/${encodeURIComponent(draft.document_id)}` : "/documents";
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <BackLink to="/documents">All documents</BackLink>
        <h2 className="text-xl font-semibold">
          {draft?.document_id ? (
            <>
              Draft of{" "}
              <Link className="underline-offset-4 hover:underline" to={documentPath}>
                {draft.document_title ?? draft.title}
              </Link>
            </>
          ) : (
            "Draft"
          )}
        </h2>
      </div>
      {notice && (
        <Alert role="status">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      )}
      {editing ? (
        <DraftEditForm
          draft={editing}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            setNotice("Draft saved.");
            setReload((n) => n + 1);
          }}
        />
      ) : (
        <DraftView
          draftId={draftId!}
          reloadKey={reload}
          onEdit={(value) => {
            setNotice("");
            setEditing(value);
          }}
          onLoaded={(value) => {
            if (value.document_id === null) navigate(`/documents/${encodeURIComponent(value.id)}`, { replace: true });
            else setDraft(value);
          }}
          onPromoted={(result) =>
            navigate(`/documents/${encodeURIComponent(result.document_id)}`, {
              state: { notice: promotedNotice(result) },
            })
          }
          onDiscarded={() => navigate(documentPath)}
        />
      )}
    </div>
  );
}
