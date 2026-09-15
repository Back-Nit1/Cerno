import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { VerdictBadge } from "@/components/verdict-badge";
import { EvidenceLevel } from "@/components/evidence-level";
import type { Evidence, Requirement, Verdict } from "@/types";

// prompt.md §18 investigation trace: requirement -> evidence gathered -> verdict -> gaps.
// No fabricated "plan"/"tool call" steps — only fields the backend actually reports.
export function RequirementDrawer({
  requirement,
  verdict,
  evidence,
  onClose,
}: {
  requirement: Requirement | null;
  verdict: Verdict | undefined;
  evidence: Evidence[];
  onClose: () => void;
}) {
  const cited = evidence.filter((e) => verdict?.evidence_ids.includes(e.id));

  return (
    <Sheet open={requirement !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
        {requirement && (
          <>
            <SheetHeader>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="font-mono text-xs">
                  {requirement.id}
                </Badge>
                <Badge variant="secondary" className="text-xs capitalize">
                  {requirement.type}
                </Badge>
                <Badge variant="secondary" className="text-xs capitalize">
                  {requirement.priority}
                </Badge>
              </div>
              <SheetTitle className="text-left">{requirement.text}</SheetTitle>
              {verdict ? (
                <div className="flex items-center gap-3 pt-1">
                  <VerdictBadge status={verdict.status} />
                  <EvidenceLevel level={verdict.evidence_level} />
                </div>
              ) : (
                <SheetDescription>Not yet analyzed.</SheetDescription>
              )}
            </SheetHeader>

            {verdict && (
              <div className="flex flex-col gap-5 px-4 pb-6">
                <p className="text-sm text-foreground">{verdict.summary}</p>

                <section>
                  <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    Evidence cited ({cited.length})
                  </h3>
                  <div className="flex flex-col gap-2">
                    {cited.length === 0 && <p className="text-sm text-muted-foreground">None.</p>}
                    {cited.map((e) => (
                      <div key={e.id} className="rounded-md border bg-card px-3 py-2 text-sm">
                        <div className="flex items-center justify-between gap-2 font-mono text-xs text-muted-foreground">
                          <span>{e.path ?? e.source}</span>
                          {e.line_start && (
                            <span>
                              L{e.line_start}
                              {e.line_end && e.line_end !== e.line_start ? `-${e.line_end}` : ""}
                            </span>
                          )}
                        </div>
                        <p className="mt-1">{e.summary}</p>
                      </div>
                    ))}
                  </div>
                </section>

                {verdict.missing_evidence.length > 0 && (
                  <section>
                    <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                      Missing evidence
                    </h3>
                    <ul className="list-inside list-disc text-sm text-muted-foreground">
                      {verdict.missing_evidence.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ul>
                  </section>
                )}

                <Separator />
                <div className="flex justify-between font-mono text-xs text-muted-foreground">
                  <span>{verdict.files_inspected.length} files inspected</span>
                  <span>{verdict.tool_calls_used} tool calls used</span>
                </div>
              </div>
            )}
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
