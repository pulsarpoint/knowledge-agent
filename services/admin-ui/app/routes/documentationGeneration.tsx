import { useParams } from 'react-router';
import AdminLayout from '../components/AdminLayout';
import ClaudeCodeSessionView from '../components/ClaudeCodeSessionView';

export default function Page() {
  const { sessionId } = useParams();
  return <AdminLayout><ClaudeCodeSessionView key={sessionId} sessionId={sessionId!} /></AdminLayout>;
}
