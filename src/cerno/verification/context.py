"""Per-analysis state shared by rules, retrieval, tools, and the agent."""

from dataclasses import dataclass, field
from pathlib import Path

from cerno.analysis import git
from cerno.analysis.graph import RepoIndex
from cerno.config import Settings
from cerno.evidence.store import EvidenceStore
from cerno.models import (
    ChangeAttribution,
    CheckResult,
    DiffSegment,
    FileChange,
    HookEvent,
    IntentContract,
    Requirement,
    SymbolChange,
)
from cerno.privacy import is_excluded, redact
from cerno.storage.sqlite import DB
from cerno.verification.runtime import run_check


@dataclass
class Context:
    root: Path
    session_id: str
    settings: Settings
    db: DB
    store: EvidenceStore
    base_tree: str
    final_tree: str
    changes: list[FileChange]
    symbol_changes: list[SymbolChange]
    base_index: RepoIndex
    final_index: RepoIndex
    events: list[HookEvent]
    contract: IntentContract
    checks: dict[str, CheckResult] = field(default_factory=dict)
    inspected_files: set[str] = field(default_factory=set)
    inspected_symbols: set[str] = field(default_factory=set)
    requirement_files: set[str] = field(default_factory=set)
    extra_expansion_used: bool = False
    lines_sent: int = 0
    tool_calls: int = 0
    segments: list[DiffSegment] = field(default_factory=list)
    attributions: dict[str, ChangeAttribution] = field(default_factory=dict)
    _diffs: dict[str, str] = field(default_factory=dict)

    def requirement(self, requirement_id: str) -> Requirement | None:
        return next((r for r in self.contract.requirements if r.id == requirement_id), None)

    def change(self, path: str) -> FileChange | None:
        return next((c for c in self.changes if c.path == path), None)

    def diff(self, path: str) -> str:
        if is_excluded(self.root, path):
            return ""
        if path not in self._diffs:
            self._diffs[path] = redact(git.get_file_diff(self.root, self.base_tree, self.final_tree, path))
        return self._diffs[path]

    def touch(self, path: str | None = None, symbol: str | None = None) -> None:
        if path:
            self.inspected_files.add(path)
            self.requirement_files.add(path)
        if symbol:
            self.inspected_symbols.add(symbol)

    def send(self, text: str) -> str:
        """Every string bound for the model passes here: redacted and counted."""
        text = redact(text)
        self.lines_sent += text.count("\n") + 1
        return text

    def check(self, kind: str) -> CheckResult | None:
        if kind in self.checks:
            return self.checks[kind]
        result = run_check(self.root, kind, self.settings.check_timeout)
        if result is None:
            return None
        verdict = "passed" if result.exit_code == 0 else "not run (error)" if result.exit_code is None else "FAILED"
        ev = self.store.add(
            f"check_{kind}", " ".join(result.command),
            f"`{' '.join(result.command)}` {verdict} (exit {result.exit_code}, {result.duration_s}s)",
            raw=f"$ {' '.join(result.command)}\nexit={result.exit_code}\n--- stdout (tail)\n{result.stdout_tail}"
                f"\n--- stderr (tail)\n{result.stderr_tail}",
        )
        result.evidence_id = ev.id
        self.checks[kind] = result
        self.db.put("project_checks", self.session_id, kind, result, seq=len(self.checks))
        return result
