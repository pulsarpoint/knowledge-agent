import { useParams } from 'react-router';
import AdminLayout from '../components/AdminLayout';
import AgentDetailView from '../components/AgentDetailView';
export default function AgentDetailPage() {
  const { agentId } = useParams();
  return <AdminLayout><AgentDetailView key={agentId} agentId={agentId!} /></AdminLayout>;
}
