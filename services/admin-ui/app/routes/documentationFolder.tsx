import { useParams } from 'react-router';
import AdminLayout from '../components/AdminLayout';
import { DocumentationFolderView } from '../components/DocumentationFoldersView';

export default function Page() {
  const folder = useParams()['*'] ?? '';
  return <AdminLayout><DocumentationFolderView folder={folder} /></AdminLayout>;
}
