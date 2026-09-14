import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import AdminUserSessionsView from "../components/AdminUserSessionsView";

export default function AdminUserPage() {
  const { email } = useParams() as { email: string };
  return <AdminLayout><AdminUserSessionsView email={email} /></AdminLayout>;
}
