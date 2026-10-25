import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import CodeProjectPage from "~/features/codeProjects/CodeProjectPage";

export default function Page() {
  const { codeProjectId } = useParams();
  return (
    <AdminLayout>
      <CodeProjectPage key={codeProjectId} codeProjectId={codeProjectId!} />
    </AdminLayout>
  );
}
