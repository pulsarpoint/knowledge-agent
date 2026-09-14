import { Link } from "react-router";
import { Button } from "~/components/ui/button";
import type { RagSourceLocation as SourceLocation } from "~/lib/rag";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "~/components/ui/card";

export default function RagSourceLocation({ location }: { location: SourceLocation }) {
  return <Card>
    <CardHeader>
      <CardTitle>Source folder</CardTitle>
      <CardDescription>The RG_DATA environment variable selects the host folder mounted into Docker.</CardDescription>
    </CardHeader>
    <CardContent>
      <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[12rem_minmax(0,1fr)]">
        <dt className="text-muted-foreground">Host folder (RG_DATA)</dt>
        <dd>{location.host_root ? <code className="break-all">{location.host_root}</code> : "Host path not provided by this deployment"}</dd>
        <dt className="text-muted-foreground">Folder in container</dt>
        <dd><code className="break-all">{location.container_root}</code></dd>
      </dl>
    </CardContent>
    <CardFooter className="flex flex-wrap items-center gap-3">
      <p className="text-sm text-muted-foreground">Collection folders live directly inside this directory. Change RG_DATA in the deployment environment and recreate the RAG containers to use a different host folder.</p>
    <Button asChild variant="outline" size="sm"><Link to="/storage?area=rag">View all storage locations</Link></Button>
    </CardFooter>
  </Card>;
}
