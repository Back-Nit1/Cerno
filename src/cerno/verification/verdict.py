"""Verdict rules. Enforced in code, whatever the model says."""

from cerno.evidence.store import EvidenceStore, level_of
from cerno.models import Verdict

RANK = {"insufficient": 0, "semantic": 1, "structural": 2, "test": 3, "runtime": 4}
QUALIFYING = {"structural", "test", "runtime"}


def finalize(verdict: Verdict, store: EvidenceStore) -> Verdict:
    ids = [i for i in dict.fromkeys(verdict.evidence_ids) if store.get(i)]
    levels = [level_of(store.get(i).type) for i in ids]
    strongest = max(levels, key=RANK.__getitem__, default="insufficient")
    status, missing = verdict.status, list(verdict.missing_evidence)
    dropped = len(set(verdict.evidence_ids)) - len(ids)
    if dropped:
        missing.append(f"{dropped} cited evidence id(s) did not exist and were ignored")
    if not ids and status != "UNCERTAIN":
        status = "UNCERTAIN"
        missing.append("verdict cited no valid evidence")
    if status == "VERIFIED" and strongest not in QUALIFYING:
        status = "LIKELY"
        missing.append("no deterministic, test, or runtime evidence: semantic support alone cannot verify")
    return verdict.model_copy(update={
        "status": status, "evidence_ids": ids, "missing_evidence": list(dict.fromkeys(missing)),
        "evidence_level": strongest if ids else "insufficient",
    })


def uncertain(requirement_id: str, summary: str, missing: list[str], evidence_ids: list[str] | None = None,
              **extra) -> Verdict:
    return Verdict(requirement_id=requirement_id, status="UNCERTAIN", evidence_level="insufficient", summary=summary,
                   evidence_ids=evidence_ids or [], missing_evidence=missing, files_inspected=[], tool_calls_used=0,
                   **extra)
