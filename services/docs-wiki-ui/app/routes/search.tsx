import { useParams } from "react-router";
import AppLayout from "../components/AppLayout";
import SearchView from "../components/SearchView";

export default function SearchPage() {
  const { app } = useParams() as { app: string };
  return <AppLayout app={app}><SearchView app={app} /></AppLayout>;
}
