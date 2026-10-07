import { Navigate } from 'react-router';

/** Sessions moved to Docs → Generate documents. */
export default function Page() {
  return <Navigate replace to="/documentation/generations" />;
}
