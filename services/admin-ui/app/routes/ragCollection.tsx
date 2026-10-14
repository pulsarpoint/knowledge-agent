import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import RagCollectionDetail from "~/features/rag/RagCollectionDetail";

export default function RagCollectionPage() {
  const params = useParams();
  return (
    <AdminLayout>
      <RagCollectionDetail id={params.collectionId!} />
    </AdminLayout>
  );
}
