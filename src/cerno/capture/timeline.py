"""Ordered prompt timeline for a session: real per-prompt records when captured by hooks,
synthesized (unclassified, no git checkpoint) for sessions that only have `Session.prompts: list[str]`."""

from cerno.models import PromptRecord, Session
from cerno.storage.sqlite import DB


def load_prompt_timeline(session_id: str, session: Session, db: DB) -> list[PromptRecord]:
    rows = db.list("prompt_timeline", session_id)
    if rows:
        return sorted((PromptRecord.model_validate(r) for r in rows), key=lambda p: p.seq)
    return [
        PromptRecord(id=f"{session_id}-P{i}", session_id=session_id, seq=i, timestamp=session.created_at, text=t)
        for i, t in enumerate(session.prompts, start=1)
    ]
