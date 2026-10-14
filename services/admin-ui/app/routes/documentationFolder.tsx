import { useParams } from 'react-router';
import AdminLayout from '../components/AdminLayout';
import { DocumentationFolderView } from '~/features/documents/DocumentationFoldersView';

export default function Page() {
  const folder = useParams()['*'] ?? '';
  return <AdminLayout><DocumentationFolderView folder={folder} /></AdminLayout>;
}
