import { useEffect, useState } from "react";
import { getSessionAudit, type AuditRow, ApiError } from "../lib/api";
import Markdown from "./Markdown";
import { Badge } from "~/components/ui/badge";
import { Card, CardContent } from "~/components/ui/card";
import { Empty, EmptyDescription } from "~/components/ui/empty";
import { Skeleton } from "~/components/ui/skeleton";

export default function AdminSessionView({ sessionKey }: { sessionKey: string }) {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setRows(null);
    setError(null);
    let stale = false;
    getSessionAudit(sessionKey)
      .then((r) => { if (!stale) setRows(r.rows); })
      .catch((e) => { if (!stale) setError(e instanceof ApiError ? e.message : "failed to load"); });
    return () => { stale = true; };
  }, [sessionKey]);
  if (error) return <Empty><EmptyDescription>{error}</EmptyDescription></Empty>;
  if (rows === null) return <Skeleton className="h-32 w-full" />;
  return (
    <div className="flex flex-col gap-4 p-4">

      {rows.length === 0
        ? <Empty><EmptyDescription>No recorded activity yet.</EmptyDescription></Empty>
        : <div className="flex flex-col gap-6">
            {rows.map((row) => (
              <div key={row.id} className="flex flex-col gap-2">
                <div className="text-sm text-muted-foreground">
                  <span className="font-medium">{row.user_email}</span>
                  {" · "}{row.ts}
                  <p className="mt-1 whitespace-pre-wrap text-foreground">{row.question}</p>
                </div>
                <Card>
                  <CardContent className="pt-6">
                    <Markdown content={row.answer} />
                    <div className="mt-2 flex flex-wrap gap-2">
                      {row.sources_used.map((s) => (
                        <Badge key={s} variant="secondary">{s}</Badge>
                      ))}
                      {row.escalated && <Badge>from code analysis</Badge>}
                      {row.not_covered && <Badge variant="outline">not covered</Badge>}
                    </div>
                  </CardContent>
                </Card>
              </div>
            ))}
          </div>}
    </div>
  );
}
