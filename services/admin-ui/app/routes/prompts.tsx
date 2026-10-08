import { Navigate, useSearchParams } from 'react-router';

export default function PromptsPage() {
  const [params] = useSearchParams();
  const agent = params.get('agent') === 'operations' ? 'operations' : params.get('agent') === 'code' ? 'code' : 'search';
  return <Navigate to={`/agents/${agent}?view=general`} replace />;
}
