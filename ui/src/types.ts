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
}

export interface IntentContract {
  task_summary: string;
  requirements: Requirement[];
  protected_areas: string[];
  explicit_acceptance_criteria: string[];
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
}

export interface AnalysisProgressStep {
  label: string;
  state: "done" | "active" | "pending";
}

// --- Repository history layer ---

export interface RepoIdentity {
  repo_id: string;
  path: string;
  remote_url: string | null;
  remote_normalized: string | null;
  default_branch: string | null;
  initial_commit: string | null;
  latest_indexed_commit: string | null;
  indexed_at: string | null;
}

export interface CommitFile {
  path: string;
  old_path: string | null;
  change_type: ChangeStatus;
  additions: number;
  deletions: number;
  is_binary: boolean;
}

export interface CommitRecord {
  hash: string;
  parents: string[];
  author_name: string;
  author_email: string;
  author_date: string;
  committer_date: string;
  subject: string;
  refs: string[];
  is_merge: boolean;
  is_root: boolean;
  files: CommitFile[];
  manifest_changed: boolean;
  schema_changed: boolean;
  test_changed: boolean;
  seq: number;
}

export interface CommitPage {
  commits: CommitRecord[];
  next_before_seq: number | null;
}

export type LinkConfidence = "direct_tool_event" | "session_attributed" | "temporal_only" | "unknown";

export interface SessionCommitLink {
  session_id: string;
  repo_id: string;
  baseline_head: string | null;
  final_head: string | null;
  commits_created: string[];
  confidence: LinkConfidence;
  basis: string;
}

export type MarkerKind = "good" | "bad" | "deployment" | "release" | "test_run" | "build" | "incident";

export interface Marker {
  id: string;
  repo_id: string;
  kind: MarkerKind;
  commit: string | null;
  timestamp: string;
  description: string;
  metadata: Record<string, unknown>;
}

export type EvidenceKind = "fact" | "correlation" | "model_inference";

export interface SuspectFinding {
  kind: "commit" | "file" | "symbol" | "dependency";
  ref: string;
  score: number;
  reason: string;
  evidence: EvidenceKind;
  session_id: string | null;
  requirement_id: string | null;
}

export interface DiagnosisResult {
  id: string;
  repo_id: string;
  good: string | null;
  bad: string | null;
  query: string | null;
  created_at: string;
  candidate_commits: string[];
  suspects: SuspectFinding[];
  narrative: string;
  model_used: boolean;
  bisect_plan: string[];
}

export interface DiagnosisRequest {
  good?: string;
  bad?: string;
  since?: string;
  until?: string;
  query?: string;
  file?: string;
  symbol?: string;
  dependency?: string;
  incident?: string;
}
