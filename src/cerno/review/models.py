"""Review result models. The JSON emitted by `cerno review` is ReviewResult.model_dump()."""

from typing import Literal

from pydantic import BaseModel

Severity = Literal["critical", "high", "medium", "low"]
RuleVerdict = Literal["VERIFIED", "LIKELY", "UNCERTAIN", "VIOLATED", "SKIPPED"]
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class ReviewRule(BaseModel):
    id: str
    text: str
    severity: Severity = "high"
    source: Literal["builtin", "file"] = "builtin"
    paths: list[str] = []  # empty: applies to every diff


class RuleOutcome(BaseModel):
    id: str
    text: str
    severity: Severity
    source: str
    verdict: RuleVerdict


class FindingEvidence(BaseModel):
    id: str
    type: str
    level: str
    path: str | None = None
    line_start: int | None = None
    summary: str


class Finding(BaseModel):
    id: str  # 12 hex chars, stable for the same rule + path + offending line text
    rule_id: str
    title: str
    severity: Severity
    status: Literal["confirmed", "suspected"]
    shown: bool
    suppressed_reason: str | None = None
    path: str
    line: int
    end_line: int
    explanation: str
    next_action: str
    evidence: list[FindingEvidence] = []


class Insufficient(BaseModel):
    rule_id: str
    summary: str
    missing_evidence: list[str] = []


class CheckSummary(BaseModel):
    kind: str
    command: str
    exit_code: int | None
    duration_s: float


class ReviewUsage(BaseModel):
    wall_time_s: float = 0.0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float | None = None  # null unless the user supplied prices; never invented


class Revision(BaseModel):
    ref: str
    sha: str | None  # null for a working tree
    tree: str


class ReviewResult(BaseModel):
    schema_version: int = 1
    base: Revision
    head: Revision
    merge_base: str
    model: str | None = None
    rules_source: list[str] = []
    findings: list[Finding] = []
    insufficient: list[Insufficient] = []
    rules: list[RuleOutcome] = []
    checks: list[CheckSummary] = []
    usage: ReviewUsage = ReviewUsage()
    errors: list[str] = []

    @property
    def shown(self) -> list[Finding]:
        return [f for f in self.findings if f.shown]
