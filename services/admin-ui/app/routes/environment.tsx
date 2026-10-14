import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import EnvironmentPage from "~/features/servers/EnvironmentPage";

export default function Page() {
  const { environmentId } = useParams();
  return (
    <AdminLayout>
      <EnvironmentPage key={environmentId} environmentId={environmentId!} />
    </AdminLayout>
  );
}
