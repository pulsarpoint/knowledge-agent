import { Navigate, useParams } from 'react-router';

/** A session link from before Generate documents: the same session under its new address. */
export default function Page() {
  const { sessionId } = useParams();
  return <Navigate replace to={`/documentation/generations/${encodeURIComponent(sessionId ?? '')}`} />;
}
