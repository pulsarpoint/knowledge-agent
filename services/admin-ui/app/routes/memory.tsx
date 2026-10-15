import AdminLayout from "../components/AdminLayout";
import MemoryView from "~/features/memory/MemoryView";

export default function MemoryRoute() {
  return (
    <AdminLayout>
      <MemoryView />
    </AdminLayout>
  );
}
