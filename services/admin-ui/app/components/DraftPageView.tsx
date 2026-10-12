import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { ArrowLeft } from 'lucide-react';
import { draftPath, type DraftDetail } from '~/lib/drafts';
import DraftEditForm from './DraftEditForm';
import DraftView from './DraftView';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';

/** /documents/drafts/:draftId — a new document's draft: preview, sources, Edit, Discard, and Promote, which creates
 * the document. A draft that now revises an existing document (its name was taken meanwhile) links to that document. */
export default function DraftPageView() {
  const { draftId } = useParams();
  const navigate = useNavigate();
  const [editing, setEditing] = useState<DraftDetail | null>(null);
  const [reload, setReload] = useState(0);
  const [notice, setNotice] = useState('');
  const [converted, setConverted] = useState<DraftDetail | null>(null);
  return <div className="flex flex-col gap-6">
    <Button asChild variant="ghost" className="self-start"><Link to="/documents"><ArrowLeft data-icon="inline-start" />Documents</Link></Button>
    <h1 className="text-2xl font-semibold">Draft of a new document</h1>
    {notice && <Alert role="status"><AlertDescription>{notice}</AlertDescription></Alert>}
    {converted && <Alert role="status"><AlertDescription>This draft now revises an existing document. <Link className="underline" to={draftPath(converted)}>Open it on its document</Link></AlertDescription></Alert>}
    {editing
      ? <DraftEditForm draft={editing} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); setNotice('Draft saved.'); setReload((n) => n + 1); }} />
      : <DraftView draftId={draftId!} reloadKey={reload} onEdit={(draft) => { setNotice(''); setEditing(draft); }}
          onLoaded={(draft) => setConverted(draft.document_id && draft.status === 'open' ? draft : null)}
          onPromoted={(result) => navigate(`/documents/${result.document_id}`)} onDiscarded={() => navigate('/documents')} />}
  </div>;
}
