import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import ServicePage from "~/features/servers/ServicePage";

export default function Page() {
  const { serviceId } = useParams();
  return (
    <AdminLayout>
      <ServicePage key={serviceId} serviceId={serviceId!} />
    </AdminLayout>
  );
}
