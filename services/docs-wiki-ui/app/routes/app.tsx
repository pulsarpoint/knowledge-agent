import { useParams } from "react-router";
import AppLayout from "../components/AppLayout";
import DocView from "../components/DocView";

export default function AppHome() {
  const { app } = useParams() as { app: string };
  return (
    <AppLayout app={app} activePath="index.md">
      <DocView app={app} path="index.md" />
    </AppLayout>
  );
}
