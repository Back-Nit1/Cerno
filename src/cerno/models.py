"""Domain models shared by every subsystem and the API."""

from typing import Literal

from pydantic import BaseModel, Field

RequirementType = Literal[
    "feature", "invariant", "constraint", "prohibition", "security", "performance", "verification", "other"
]
Status = Literal["VERIFIED", "LIKELY", "UNCERTAIN", "VIOLATED"]
EvidenceLevel = Literal["runtime", "test", "structural", "semantic", "insufficient"]
CheckKind = Literal["test", "build", "lint", "typecheck"]
PromptClassification = Literal["new_task", "refinement", "correction", "replacement", "continuation", "ambiguous"]


class Requirement(BaseModel):
    id: str
    text: str
    type: RequirementType
    priority: Literal["critical", "high", "normal"]
    explicit: bool
    verification_hint: str | None = None
    # Provenance (backward-compatible: all default so pre-existing constructions are unaffected).
    origin_prompt_id: str | None = None  # which prompt introduced this requirement
    refined_by: list[str] = []  # prompt ids that refined it without replacing it
    superseded_by: str | None = None  # prompt id that superseded it, if any
    active: bool = True  # superseded requirements are kept for history but are not active
    segment_ids: list[str] = []  # prompt-to-prompt diff segments relevant to this requirement


class IntentContract(BaseModel):
    task_summary: str
    requirements: list[Requirement]
    protected_areas: list[str] = []
    explicit_acceptance_criteria: list[str] = []
    ambiguities: list[str] = []  # prompt relationships or requirements needing developer review


class PromptRecord(BaseModel):
    """One structured UserPromptSubmit event. Classification fields are filled in by `cerno analyze`,
    never by hooks: hooks only ever capture text and git state, so they stay model-call-free."""

    id: str
    session_id: str
    seq: int
    timestamp: str
    text: str
    head: str | None = None  # git HEAD at capture time
    tree: str | None = None  # worktree tree id at capture time (the prompt's git checkpoint)
    classification: PromptClassification | None = None
    refines: list[str] = []  # prompt ids this prompt refines
    supersedes: list[str] = []  # prompt ids this prompt supersedes
    confidence: float | None = None
    ambiguous: bool = False


class ChangeAttribution(BaseModel):
    """Temporal attribution of one changed file to the prompt that immediately preceded its change.
    Evidence of correlation in time, not proof of causality, unless a tool event gives direct evidence."""

    path: str
    prompt_id: str | None
    segment_id: str | None
    confidence: Literal["direct", "temporal"]
    basis: str


class HookEvent(BaseModel):
    event_id: str
    session_id: str
    event_type: str
    timestamp: str
    tool_name: str | None = None
    path: str | None = None
    command: str | None = None
    success: bool | None = None
    raw_payload_path: str | None = None


class Snapshot(BaseModel):
    kind: Literal["baseline", "final", "prompt"]
    repo_root: str
    head: str | None
    tree: str | None  # git tree object of the whole working tree (tracked + untracked, .gitignore honored)
    status: list[str]
    dirty_files: list[str]
    manifest_hashes: dict[str, str]
    schema_hashes: dict[str, str]
    project_types: list[str]
    scripts: dict[str, list[str]]
    timestamp: str
    prompt_id: str | None = None  # set when kind == "prompt"


class FileChange(BaseModel):
    path: str
    status: Literal["created", "modified", "deleted", "renamed"]
    additions: int
    deletions: int
    baseline_hash: str | None
    final_hash: str | None
    old_path: str | None = None


class SymbolChange(BaseModel):
    file_path: str
    symbol_name: str
    symbol_type: str
    change_type: Literal["added", "modified", "deleted"]
    start_line: int
    end_line: int
    callers: list[str] = []
    callees: list[str] = []


class DiffSegment(BaseModel):
    """A git-tree-to-git-tree diff between two consecutive checkpoints, attributed to the prompt that opened it."""

    id: str
    from_label: str
    to_label: str
    from_tree: str
    to_tree: str
    prompt_id: str | None  # the prompt Claude was acting on during this segment, if any
    files: list[FileChange] = []
    symbols: list[SymbolChange] = []


class Evidence(BaseModel):
    id: str
    session_id: str
    type: str  # check_test | check_build | check_lint | check_typecheck | rule | diff | symbol | code | ...
    source: str
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    summary: str
    raw_ref: str | None = None
    content_hash: str


class CheckResult(BaseModel):
    kind: CheckKind
    command: list[str]
    exit_code: int | None  # None = could not run / timed out
    duration_s: float
    stdout_tail: str
    stderr_tail: str
    evidence_id: str | None = None


class TraceStep(BaseModel):
    tool: str
    input: dict
    summary: str
    evidence_ids: list[str] = []


class Verdict(BaseModel):
    requirement_id: str
    status: Status
    evidence_level: EvidenceLevel
    summary: str
    evidence_ids: list[str]
    missing_evidence: list[str]
    files_inspected: list[str]
    tool_calls_used: int
    plan: str = ""
    trace: list[TraceStep] = []
    cached: bool = False


class ChangeAssessment(BaseModel):
    path: str
    classification: Literal["EXPECTED", "EXPLAINABLE", "UNEXPLAINED", "CONTRADICTORY"]
    impact: Literal["HIGH", "MEDIUM", "LOW"]
    what_changed: str
    why_impact: str
    affected_requirements: list[str] = []
    agent_explanation: str | None = None


class AgentClaim(BaseModel):
    id: str
    text: str
    category: str
    result: Literal["CONFIRMED", "CONTRADICTED", "UNVERIFIABLE"]
    evidence_ids: list[str] = []
    summary: str = ""


class UsageStats(BaseModel):
    model_input_tokens: int = 0
    model_output_tokens: int = 0
    cache_read_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    repo_files: int = 0
    repo_lines: int | None = None
    files_inspected: int = 0
    symbols_inspected: int = 0
    requirements_checked: int = 0
    cache_hits: int = 0
    lines_sent_to_model: int = 0
    context_avoided_pct: float | None = None


class Session(BaseModel):
    id: str
    repo_root: str
    status: str = "active"  # active | stopped | analyzing | analyzed | failed
    prompts: list[str] = []
    transcript_path: str | None = None
    created_at: str
    updated_at: str
    task_summary: str | None = None
    intent_source: str | None = None  # model | fallback
    counts: dict[str, int] = Field(default_factory=dict)
    progress: list[dict] = []
    error: str | None = None
