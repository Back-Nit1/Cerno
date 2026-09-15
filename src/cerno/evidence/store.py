"""Evidence with stable, content-derived ids and preserved provenance."""

import hashlib

from cerno.models import Evidence
from cerno.privacy import redact
from cerno.storage.sqlite import DB

LEVELS = {
    "check_test": "test",
    "check_build": "runtime",
    "check_lint": "runtime",
    "check_typecheck": "runtime",
    "rule": "structural",
}


def level_of(evidence_type: str) -> str:
    """Anything a tool merely *showed* the model (code, diffs, search hits) is only semantic support."""
    return LEVELS.get(evidence_type, "semantic")


class EvidenceStore:
    def __init__(self, db: DB, session_id: str):
        self.db, self.session_id = db, session_id
        self.items: dict[str, Evidence] = {}
        self.raw: dict[str, str] = {}

    def add(self, type: str, source: str, summary: str, path: str | None = None, line_start: int | None = None,
            line_end: int | None = None, raw: str | None = None) -> Evidence:
        body = redact(raw if raw is not None else summary)
        digest = hashlib.sha1(body.encode()).hexdigest()
        ident = hashlib.sha1(f"{type}|{source}|{path}|{line_start}|{digest}".encode()).hexdigest()[:10]
        ev_id = f"E-{ident}"
        if ev_id not in self.items:
            ev = Evidence(id=ev_id, session_id=self.session_id, type=type, source=source, path=path,
                          line_start=line_start, line_end=line_end, summary=redact(summary)[:500],
                          raw_ref=f"evidence:{ev_id}" if raw is not None else None, content_hash=digest)
            self.items[ev_id] = ev
            self.raw[ev_id] = body
            self.db.put("evidence", self.session_id, ev_id, {**ev.model_dump(), "raw": body[:20000]},
                        seq=len(self.items))
        return self.items[ev_id]

    def restore(self, rows: list[dict]) -> None:
        """Re-register evidence from a cached verdict so its citations stay valid."""
        for row in rows:
            ev = Evidence.model_validate(row)
            if ev.id not in self.items:
                self.items[ev.id] = ev
                self.raw[ev.id] = row.get("raw", "")
                self.db.put("evidence", self.session_id, ev.id, row, seq=len(self.items))

    def get(self, ev_id: str) -> Evidence | None:
        return self.items.get(ev_id)

    def dump(self, ids: list[str]) -> list[dict]:
        return [{**self.items[i].model_dump(), "raw": self.raw.get(i, "")[:20000]} for i in ids if i in self.items]
