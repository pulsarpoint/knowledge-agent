import { useParams } from 'react-router';
import AdminLayout from '../components/AdminLayout';
import SessionView from '../components/SessionView';

export default function Page() {
  const { sessionId } = useParams();
  return <AdminLayout><SessionView key={sessionId} sessionId={sessionId!} /></AdminLayout>;
}
