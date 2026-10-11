import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import DiscoveryRunView from "../components/DiscoveryRunView";

export default function Page() {
  const { serverId, runId } = useParams();
  return <AdminLayout><DiscoveryRunView key={runId} serverId={serverId!} runId={runId!} /></AdminLayout>;
}
