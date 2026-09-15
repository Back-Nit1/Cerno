import { Link } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFetch } from "@/hooks/useFetch";
import { api } from "@/lib/api";

export function SessionsPage() {
  const { data: sessions, error, loading } = useFetch(api.sessions, []);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-8">
      <h1 className="font-mono text-lg font-medium text-heading">Cerno · Sessions</h1>

      {loading && <Skeleton className="h-20 w-full" />}
      {error && <p className="text-sm text-violated">{error}</p>}
      {sessions?.length === 0 && (
        <p className="text-sm text-muted-foreground">No sessions captured yet. Run a Claude Code task in a `cerno init` repo.</p>
      )}

      {sessions?.map((s) => (
        <Link key={s.id} to={`/sessions/${s.id}`}>
          <Card className="transition-colors hover:bg-card/70">
            <CardContent className="flex items-center justify-between">
              <div>
                <p className="font-medium">{s.task_summary}</p>
                <p className="font-mono text-xs text-muted-foreground">{new Date(s.created_at).toLocaleString()}</p>
              </div>
              <div className="flex gap-3 font-mono text-xs">
                <span className="text-verified">{s.verified_count} verified</span>
                <span className="text-likely">{s.likely_count} likely</span>
                <span className="text-uncertain">{s.uncertain_count} uncertain</span>
                <span className="text-violated">{s.violated_count} violated</span>
              </div>
            </CardContent>
          </Card>
        </Link>
      ))}
    </div>
  );
}
