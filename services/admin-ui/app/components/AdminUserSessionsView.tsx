import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getAuditUserSessions, type AuditSession, ApiError } from "../lib/api";
import { Empty, EmptyDescription } from "~/components/ui/empty";
import { Skeleton } from "~/components/ui/skeleton";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "~/components/ui/table";

export default function AdminUserSessionsView({ email }: { email: string }) {
  const [sessions, setSessions] = useState<AuditSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setSessions(null);
    setError(null);
    let stale = false;
    getAuditUserSessions(email)
      .then((r) => { if (!stale) setSessions(r.sessions); })
      .catch((e) => { if (!stale) setError(e instanceof ApiError ? e.message : "failed to load"); });
    return () => { stale = true; };
  }, [email]);
  if (error) return <Empty><EmptyDescription>{error}</EmptyDescription></Empty>;
  if (sessions === null) return <Skeleton className="h-32 w-full" />;
  return (
    <div className="flex flex-col gap-4 p-4">

      <h2 className="text-sm text-muted-foreground">Sessions for {email}</h2>
      {sessions.length === 0
        ? <Empty><EmptyDescription>No recorded activity yet.</EmptyDescription></Empty>
        : <Table>
            <TableHeader><TableRow>
              <TableHead>First question</TableHead><TableHead>Questions</TableHead>
              <TableHead>First</TableHead><TableHead>Last</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {sessions.map((s) => (
                <TableRow key={s.session_key}>
                  <TableCell>
                    <Link className="hover:underline font-medium"
                          to={`/activity/session/${encodeURIComponent(s.session_key)}`}>
                      {s.first_question}
                    </Link>
                  </TableCell>
                  <TableCell>{s.questions}</TableCell>
                  <TableCell className="text-muted-foreground">{s.first_ts}</TableCell>
                  <TableCell className="text-muted-foreground">{s.last_ts}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>}
    </div>
  );
}
