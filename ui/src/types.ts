// Mirrors backend Pydantic models (prompt.md §3, §4, §5, §11, §14).
// The UI holds no verification logic — these types are the wire contract.

export type RequirementType =
  | "feature"
  | "invariant"
  | "constraint"
  | "prohibition"
  | "security"
  | "performance"
  | "verification"
  | "other";

export type Priority = "critical" | "high" | "normal";

export interface Requirement {
  id: string;
  text: string;
  type: RequirementType;
  priority: Priority;
  explicit: boolean;
  verification_hint: string | null;
  origin_prompt_id: string | null;
  refined_by: string[];
  superseded_by: string | null;
  active: boolean;
  segment_ids: string[];
}

export interface IntentContract {
  task_summary: string;
  requirements: Requirement[];
  protected_areas: string[];
  explicit_acceptance_criteria: string[];
  ambiguities: string[];
}

export type PromptClassification =
  | "new_task"
  | "refinement"
  | "correction"
  | "replacement"
  | "continuation"
  | "ambiguous";

export interface PromptTimelineEntry {
  id: string;
  session_id: string;
  seq: number;
  timestamp: string;
  text: string;
  head: string | null;
  tree: string | null;
  classification: PromptClassification | null;
  refines: string[];
  supersedes: string[];
  confidence: number | null;
  ambiguous: boolean;
  files_changed: string[];
  symbols_changed: string[];
  requirements_introduced: string[];
  requirements_refined: string[];
  requirements_superseded: string[];
}

export interface DiffSegmentSummary {
  id: string;
  from_label: string;
  to_label: string;
  prompt_id: string | null;
  files: FileChange[];
  symbols: SymbolChange[];
}

export type ChangeStatus = "created" | "modified" | "deleted" | "renamed";

export interface FileChange {
  path: string;
  status: ChangeStatus;
  additions: number;
  deletions: number;
  baseline_hash: string | null;
  final_hash: string | null;
}

export type SymbolChangeType = "added" | "modified" | "deleted";

export interface SymbolChange {
  file_path: string;
  symbol_name: string;
  symbol_type: string;
  change_type: SymbolChangeType;
  start_line: number;
  end_line: number;
  callers: string[];
  callees: string[];
}

export interface Evidence {
  id: string;
  session_id: string;
  type: string;
  source: string;
  path: string | null;
  line_start: number | null;
  line_end: number | null;
  summary: string;
  raw_ref: string | null;
  content_hash: string;
}

export type VerdictStatus = "VERIFIED" | "LIKELY" | "UNCERTAIN" | "VIOLATED";

export type EvidenceLevel = "runtime" | "test" | "structural" | "semantic" | "insufficient";

export interface Verdict {
  requirement_id: string;
  status: VerdictStatus;
  evidence_level: EvidenceLevel;
  summary: string;
  evidence_ids: string[];
  missing_evidence: string[];
  files_inspected: string[];
  tool_calls_used: number;
}

export type ChangeClassification = "EXPECTED" | "EXPLAINABLE" | "UNEXPLAINED" | "CONTRADICTORY";

export interface UnexpectedChange {
  path: string;
  classification: ChangeClassification;
  impact: "HIGH" | "MEDIUM" | "LOW";
  reason: string;
  related_requirement_id: string | null;
}

export type CheckKind = "test" | "build" | "lint" | "typecheck";

export interface ProjectCheck {
  kind: CheckKind;
  command: string;
  exit_code: number;
  duration_ms: number;
  stdout_tail: string;
  stderr_tail: string;
}

export type ClaimResult = "CONFIRMED" | "CONTRADICTED" | "UNVERIFIABLE";

export interface AgentClaim {
  text: string;
  result: ClaimResult;
  evidence_ids: string[];
}

export interface UsageMetrics {
  model_input_tokens: number;
  model_output_tokens: number;
  model_calls: number;
  tool_calls: number;
  files_in_repository: number;
  files_inspected: number;
  symbols_inspected: number;
  requirements_checked: number;
  cache_hits: number;
}

export type SessionStatus = "capturing" | "analyzing" | "complete" | "failed";

export interface SessionSummary {
  id: string;
  task_summary: string;
  status: SessionStatus;
  created_at: string;
  verified_count: number;
  likely_count: number;
  uncertain_count: number;
  violated_count: number;
}

export interface SessionDetail {
  id: string;
  status: SessionStatus;
  created_at: string;
  repository_root: string;
  head_commit: string;
  intent: IntentContract;
  verdicts: Verdict[];
  file_changes: FileChange[];
  symbol_changes: SymbolChange[];
  unexpected_changes: UnexpectedChange[];
  project_checks: ProjectCheck[];
  agent_claims: AgentClaim[];
  usage: UsageMetrics;
  prompt_timeline: PromptTimelineEntry[];
}

export interface AnalysisProgressStep {
  label: string;
  state: "done" | "active" | "pending";
}
