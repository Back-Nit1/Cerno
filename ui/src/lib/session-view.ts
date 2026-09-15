import type { AnalysisProgressStep, PromptClassification, SessionStatus, Verdict, VerdictStatus } from "@/types";

const CLASSIFICATION_LABELS: Record<PromptClassification, string> = {
  new_task: "new task",
  refinement: "refinement",
  correction: "correction",
  replacement: "replacement",
  continuation: "continuation",
  ambiguous: "ambiguous",
};

export function classificationLabel(classification: PromptClassification | null): string {
  return classification ? CLASSIFICATION_LABELS[classification] : "unclassified";
}

const STAGES = ["Change analysis", "Evidence retrieval", "Verification agent", "Verdict"];

export function progressSteps(status: SessionStatus): AnalysisProgressStep[] {
  const activeIndex = status === "capturing" ? 0 : status === "analyzing" ? 2 : STAGES.length;
  return STAGES.map((label, i) => ({
    label,
    state: i < activeIndex ? "done" : i === activeIndex ? "active" : "pending",
  }));
}

export function countByStatus(verdicts: Verdict[]): Record<VerdictStatus, number> {
  const counts: Record<VerdictStatus, number> = { VERIFIED: 0, LIKELY: 0, UNCERTAIN: 0, VIOLATED: 0 };
  for (const v of verdicts) counts[v.status]++;
  return counts;
}
