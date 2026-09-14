import { useParams } from "react-router";
import AppLayout from "../components/AppLayout";
import GapsView from "../components/GapsView";

export default function GapsPage() {
  const { app } = useParams() as { app: string };
  return <AppLayout app={app}><GapsView app={app} /></AppLayout>;
}
