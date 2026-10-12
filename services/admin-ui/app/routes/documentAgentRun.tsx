import AdminLayout from '../components/AdminLayout';
import DocumentEditorView from '../components/DocumentEditorView';

/** An Ask agent run's link: the document page with the agent pane open on that run. */
export default function Page() {
  return <AdminLayout><DocumentEditorView /></AdminLayout>;
}
