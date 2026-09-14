import { useParams } from "react-router";
import AppLayout from "../components/AppLayout";
import DocView from "../components/DocView";

export default function DocPage() {
  const params = useParams() as { app: string; "*": string };
  return (
    <AppLayout app={params.app} activePath={params["*"]}>
      <DocView app={params.app} path={params["*"]} />
    </AppLayout>
  );
}
