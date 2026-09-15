import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFetch } from "@/hooks/useFetch";
import { historyApi } from "@/lib/api";
import type { CommitPage, DiagnosisResult, EvidenceKind, Marker } from "@/types";

const EVIDENCE_COLOR: Record<EvidenceKind, string> = {
  fact: "text-verified border-verified/40 bg-verified/10",
  correlation: "text-uncertain border-uncertain/40 bg-uncertain/10",
  model_inference: "text-likely border-likely/40 bg-likely/10",
};

function CommitRow({ hash, subject, author_date, is_merge, files, session_id }: CommitPage["commits"][number] & { session_id?: string | null }) {
  const adds = files.reduce((n, f) => n + f.additions, 0);
  const dels = files.reduce((n, f) => n + f.deletions, 0);
  return (
    <div className="flex items-center justify-between border-b border-border py-2 text-sm last:border-0">
      <div className="min-w-0 flex-1">
        <p className="truncate">
          {is_merge && <Badge variant="outline" className="mr-2 text-xs">merge</Badge>}
          {subject}
        </p>
        <p className="font-mono text-xs text-muted-foreground">
          {hash.slice(0, 10)} · {author_date.slice(0, 10)}
          {session_id && (
            <>
              {" · "}
              <Link to={`/sessions/${session_id}`} className="underline">session {session_id.slice(0, 12)}</Link>
            </>
          )}
        </p>
      </div>
      <div className="ml-4 shrink-0 font-mono text-xs">
        <span className="text-verified">+{adds}</span> <span className="text-violated">-{dels}</span>
      </div>
    </div>
  );
}

function MarkersPanel({ markers }: { markers: Marker[] }) {
  if (markers.length === 0) return <p className="text-sm text-muted-foreground">No markers recorded yet.</p>;
  return (
    <div className="flex flex-col gap-1">
      {markers.map((m) => (
        <div key={m.id} className="flex items-center gap-2 text-xs">
          <Badge variant="outline" className="uppercase">{m.kind}</Badge>
          <span className="font-mono text-muted-foreground">{(m.commit ?? "").slice(0, 10)}</span>
          <span>{m.description}</span>
        </div>
      ))}
    </div>
  );
}

function DiagnosePanel() {
  const [file, setFile] = useState("");
  const [query, setQuery] = useState("");
  const [good, setGood] = useState("");
  const [bad, setBad] = useState("");
  const [result, setResult] = useState<DiagnosisResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      setResult(await historyApi.diagnose({
        file: file || undefined, query: query || undefined, good: good || undefined, bad: bad || undefined,
      }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const inputCls = "rounded-md border border-input bg-background px-2 py-1 text-sm";
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <input className={inputCls} placeholder="known-good commit" value={good} onChange={(e) => setGood(e.target.value)} />
        <input className={inputCls} placeholder="known-bad commit" value={bad} onChange={(e) => setBad(e.target.value)} />
        <input className={inputCls} placeholder="suspected file" value={file} onChange={(e) => setFile(e.target.value)} />
        <input className={`${inputCls} flex-1`} placeholder="error text / query" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button onClick={run} disabled={running}>{running ? "Diagnosing…" : "Diagnose"}</Button>
      </div>
      {error && <p className="text-sm text-violated">{error}</p>}
      {result && (
        <Card>
          <CardContent className="flex flex-col gap-3">
            <p className="text-sm">{result.narrative}</p>
            <p className="font-mono text-xs text-muted-foreground">model used: {String(result.model_used)}</p>
            {result.suspects.map((s) => (
              <div key={`${s.kind}:${s.ref}`} className="flex items-center gap-2 text-xs">
                <Badge className={EVIDENCE_COLOR[s.evidence]} variant="outline">{s.evidence}</Badge>
                <span className="font-mono">{s.score.toFixed(1)}</span>
                <span className="font-mono">{s.kind === "commit" ? s.ref.slice(0, 10) : s.ref}</span>
                <span className="text-muted-foreground">{s.reason}</span>
              </div>
            ))}
            {result.bisect_plan.length > 0 && (
              <div className="rounded-md bg-muted p-2 font-mono text-xs">
                <p className="mb-1 text-muted-foreground">Suggested manual bisect (not run automatically):</p>
                {result.bisect_plan.map((line, i) => <p key={i}>{line}</p>)}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export function HistoryPage() {
  const { data: status } = useFetch(historyApi.status, []);
  const { data: markers } = useFetch(historyApi.markers, []);
  const [page, setPage] = useState<CommitPage | null>(null);
  const { data: firstPage, loading } = useFetch(() => historyApi.commits(), []);
  const commits = page ?? firstPage;

  const loadMore = async () => {
    if (!commits?.next_before_seq) return;
    const next = await historyApi.commits(commits.next_before_seq);
    setPage({ commits: [...commits.commits, ...next.commits], next_before_seq: next.next_before_seq });
  };

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-8">
      <h1 className="font-mono text-lg font-medium text-heading">Cerno · Repository History</h1>
      {status && (
        <p className="font-mono text-xs text-muted-foreground">
          repo {status.repo_id.slice(0, 12)} · branch {status.default_branch ?? "?"} · latest{" "}
          {(status.latest_indexed_commit ?? "(not indexed)").slice(0, 10)}
        </p>
      )}

      <Card>
        <CardContent>
          <h2 className="mb-2 font-mono text-sm font-medium">Diagnose a regression</h2>
          <DiagnosePanel />
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <h2 className="mb-2 font-mono text-sm font-medium">Markers</h2>
          <MarkersPanel markers={markers ?? []} />
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <h2 className="mb-2 font-mono text-sm font-medium">Commit timeline</h2>
          {loading && !commits && <Skeleton className="h-20 w-full" />}
          {commits?.commits.map((c) => <CommitRow key={c.hash} {...c} />)}
          {commits?.next_before_seq != null && (
            <Button variant="outline" className="mt-2" onClick={loadMore}>Load more</Button>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
