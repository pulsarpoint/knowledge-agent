import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { ApiError, getApps, type AppInfo } from "../lib/api";
import { Card, CardHeader, CardTitle } from "~/components/ui/card";

export default function Home() {
  const [apps, setApps] = useState<AppInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  useEffect(() => {
    getApps()
      .then((a) => { setApps(a); setError(null); })
      .catch((e) => {
        setError(e instanceof ApiError ? e.message : "couldn't load applications");
        setApps([]);
      });
  }, []);
  useEffect(() => {
    if (apps?.length === 1) navigate(`/${apps[0].name}`, { replace: true });
  }, [apps, navigate]);
  if (!apps) return <main className="p-8 text-sm text-muted-foreground">Loading…</main>;
  return (
    <main className="mx-auto max-w-xl p-8">
      <h1 className="mb-6 text-2xl font-bold">Knowledge Agent v3</h1>
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}
      {!error && apps.length === 0 && (
        <p className="text-sm text-muted-foreground">No applications configured.</p>
      )}
      <ul className="flex flex-col gap-2">
        {apps.map((a) => (
          <li key={a.name}>
            <Link to={`/${a.name}`} className="block">
              <Card className="transition-shadow hover:ring-2 hover:ring-ring">
                <CardHeader>
                  <CardTitle className="text-base">{a.name}</CardTitle>
                </CardHeader>
              </Card>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
