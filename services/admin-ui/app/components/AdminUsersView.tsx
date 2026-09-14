import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getAuditUsers, type AuditUser, ApiError } from "../lib/api";
import { Empty, EmptyDescription } from "~/components/ui/empty";
import { Skeleton } from "~/components/ui/skeleton";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "~/components/ui/table";

export default function AdminUsersView() {
  const [users, setUsers] = useState<AuditUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setUsers(null);
    setError(null);
    let stale = false;
    getAuditUsers()
      .then((r) => { if (!stale) setUsers(r.users); })
      .catch((e) => { if (!stale) setError(e instanceof ApiError ? e.message : "failed to load"); });
    return () => { stale = true; };
  }, []);
  if (error) return <Empty><EmptyDescription>{error}</EmptyDescription></Empty>;
  if (users === null) return <Skeleton className="h-32 w-full" />;
  return (
    <div className="flex flex-col gap-4 p-4">

      {users.length === 0
        ? <Empty><EmptyDescription>No recorded activity yet.</EmptyDescription></Empty>
        : <Table>
            <TableHeader><TableRow>
              <TableHead>User</TableHead><TableHead>Sessions</TableHead>
              <TableHead>Questions</TableHead><TableHead>Last activity</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.user_email}>
                  <TableCell>
                    <Link className="hover:underline font-medium"
                          to={`/activity/user/${encodeURIComponent(u.user_email)}`}>
                      {u.user_email}
                    </Link>
                  </TableCell>
                  <TableCell>{u.sessions}</TableCell>
                  <TableCell>{u.questions}</TableCell>
                  <TableCell className="text-muted-foreground">{u.last_ts}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>}
    </div>
  );
}
