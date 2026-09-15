import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { VerdictBadge } from "@/components/verdict-badge";
import { EvidenceLevel } from "@/components/evidence-level";
import { AnalysisProgress } from "@/components/analysis-progress";
import { RequirementDrawer } from "@/components/requirement-drawer";
import { useFetch } from "@/hooks/useFetch";
import { api } from "@/lib/api";
import { countByStatus, progressSteps } from "@/lib/session-view";

export function SessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [refreshKey, setRefreshKey] = useState(0);
  const [openRequirementId, setOpenRequirementId] = useState<string | null>(null);

  const { data: session, error, loading } = useFetch(() => api.session(sessionId!), [sessionId, refreshKey]);
  const { data: evidence } = useFetch(() => api.evidence(sessionId!), [sessionId, refreshKey]);

  const counts = useMemo(() => countByStatus(session?.verdicts ?? []), [session]);

  if (loading) return <Skeleton className="m-8 h-64" />;
  if (error) return <p className="p-8 text-sm text-violated">{error}</p>;
  if (!session) return null;

  const verdictByReq = new Map(session.verdicts.map((v) => [v.requirement_id, v]));
  const openRequirement = session.intent.requirements.find((r) => r.id === openRequirementId) ?? null;
  const usage = session.usage;
  const coveragePct = usage.files_in_repository > 0 ? Math.round((1 - usage.files_inspected / usage.files_in_repository) * 100) : null;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-medium text-heading">{session.intent.task_summary}</h1>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {counts.VERIFIED} VERIFIED · {counts.LIKELY} LIKELY · {counts.UNCERTAIN} UNCERTAIN · {counts.VIOLATED} VIOLATED
          </p>
        </div>
        {session.status !== "complete" && (
          <Button
            size="sm"
            onClick={() => api.analyze(session.id).then(() => setRefreshKey((k) => k + 1))}
          >
            Run analysis
          </Button>
        )}
      </header>

      {session.status !== "complete" && (
        <Card>
          <CardContent>
            <AnalysisProgress steps={progressSteps(session.status)} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Requirements</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1">
          {session.intent.requirements.map((r) => {
            const v = verdictByReq.get(r.id);
            return (
              <button
                key={r.id}
                onClick={() => setOpenRequirementId(r.id)}
                className="flex items-center justify-between gap-4 rounded-md px-2 py-2 text-left text-sm hover:bg-secondary"
              >
                <span className="flex-1">{r.text}</span>
                {v ? (
                  <span className="flex items-center gap-3">
                    <EvidenceLevel level={v.evidence_level} className="hidden sm:inline-flex" />
                    <VerdictBadge status={v.status} />
                  </span>
                ) : (
                  <span className="font-mono text-xs text-muted-foreground">pending</span>
                )}
              </button>
            );
          })}
        </CardContent>
      </Card>

      {session.unexpected_changes.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Unexpected changes</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-1">
            {session.unexpected_changes.map((c) => (
              <div key={c.path} className="flex items-center justify-between gap-4 py-1 text-sm">
                <span className="font-mono text-xs">{c.path}</span>
                <span className="flex items-center gap-2 text-xs text-muted-foreground">
                  {c.classification}
                  <span className={c.impact === "HIGH" ? "text-violated" : c.impact === "MEDIUM" ? "text-uncertain" : ""}>
                    {c.impact}
                  </span>
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {session.project_checks.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Runtime</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-1">
            {session.project_checks.map((c) => (
              <div key={c.kind} className="flex items-center justify-between text-sm">
                <span className="capitalize">{c.kind}</span>
                <span className={c.exit_code === 0 ? "text-verified" : "text-violated"}>
                  {c.exit_code === 0 ? "pass" : `exit ${c.exit_code}`}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Analysis efficiency</CardTitle>
        </CardHeader>
        <CardContent className="font-mono text-xs text-muted-foreground">
          {usage.files_in_repository} repo files · {usage.files_inspected} inspected ·{" "}
          {usage.model_input_tokens.toLocaleString()} input tokens
          {coveragePct !== null && ` · ~${coveragePct}% context avoided`}
        </CardContent>
      </Card>

      <RequirementDrawer
        requirement={openRequirement}
        verdict={openRequirementId ? verdictByReq.get(openRequirementId) : undefined}
        evidence={evidence ?? []}
        onClose={() => setOpenRequirementId(null)}
      />
    </div>
  );
}
