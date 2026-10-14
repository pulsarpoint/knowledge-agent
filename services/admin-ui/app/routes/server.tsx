import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import ServerPage from "~/features/servers/ServerPage";

export default function Page() {
  const { serverId } = useParams();
  return (
    <AdminLayout>
      <ServerPage key={serverId} serverId={serverId!} />
    </AdminLayout>
  );
}
