import { useParams } from 'react-router';
import AdminLayout from '../components/AdminLayout';
import GenerationView from '../components/GenerationView';

export default function Page() {
  const { sessionId } = useParams();
  return <AdminLayout><GenerationView key={sessionId} sessionId={sessionId!} /></AdminLayout>;
}
