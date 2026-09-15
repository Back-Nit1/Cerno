"""History-layer API routes: repo status, paginated commit timeline, file/symbol history,
session-commit relationships, markers, and diagnosis. Nothing here sends a whole commit
history to the browser in one response -- /commits is cursor-paginated."""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from cerno.analysis import git
from cerno.analysis import history as hist
from cerno.config import Settings
from cerno.models import Session
from cerno.storage.sqlite import DB
from cerno.verification.diagnosis import diagnose as run_diagnose_
from cerno.verification.diagnosis import get_diagnosis

PAGE_MAX = 200


def history_router(root: Path, db: DB, settings: Settings) -> APIRouter:
    r = APIRouter(prefix="/api/history", tags=["history"])
    repo_id = hist.repo_id_for(root)

    @r.get("/status")
    def status():
        identity = db.get("repo_identity", repo_id, "identity")
        return identity or {"repo_id": repo_id, "path": str(root), "latest_indexed_commit": None,
                            "initial_commit": None, "indexed_at": None}

    @r.post("/index")
    def reindex(limit: int = 2000):
        return hist.index(root, db, limit=limit).model_dump()

    @r.get("/commits")
    def commits(limit: int = 50, before_seq: int | None = None, first_parent: bool = False):
        limit = min(limit, PAGE_MAX)
        rows = (hist.first_parent_log(db, repo_id, limit=limit) if first_parent
               else hist.log(db, repo_id, limit=limit, before_seq=before_seq))
        return {"commits": [c.model_dump() for c in rows],
               "next_before_seq": rows[-1].seq if len(rows) == limit else None}

    @r.get("/commits/{commit_hash}")
    def commit_detail(commit_hash: str):
        c = hist.get_commit(db, repo_id, commit_hash)
        if c is None:
            raise HTTPException(404, f"commit {commit_hash} not indexed")
        session_id = next((row["session_id"] for row in db.list("session_commit_links", repo_id)
                          if commit_hash in row.get("commits_created", [])), None)
        return {**c.model_dump(), "session_id": session_id}

    @r.get("/path")
    def path_history(path: str):
        rows = hist.path_history(db, repo_id, path)
        return {"path": path, "commits": [c.model_dump() for c in rows], **hist.creation_and_deletion(rows, path)}

    @r.get("/symbol")
    def symbol_history(path: str, limit: int = 20):
        from cerno.analysis.graph import RepoIndex
        from cerno.analysis.symbols import changed_symbols
        from cerno.models import FileChange

        commits = sorted(hist.path_history(db, repo_id, path), key=lambda c: c.seq)[-limit:]
        cache: dict[str, RepoIndex] = {}

        def idx(ref: str) -> RepoIndex:
            if ref not in cache:
                cache[ref] = RepoIndex.from_tree(root, ref, db)
            return cache[ref]

        out = []
        for c in commits:
            parent = c.parents[0] if c.parents else hist.EMPTY_TREE
            hit = next((f for f in c.files if f.path == path or f.old_path == path), None)
            fc = [FileChange(path=hit.path, status=hit.change_type, additions=hit.additions,
                            deletions=hit.deletions, baseline_hash=None, final_hash=None,
                            old_path=hit.old_path)] if hit else []
            syms = changed_symbols(fc, idx(parent), idx(c.hash)) if fc else []
            out.append({"commit": c.hash, "author_date": c.author_date, "subject": c.subject,
                       "symbols": [s.model_dump() for s in syms]})
        return {"path": path, "history": out}

    @r.get("/sessions/{session_id}")
    def session_history(session_id: str):
        link = hist.get_session_link(db, repo_id, session_id)
        if link is None:
            stored = db.get("sessions", session_id, session_id)
            if stored is None:
                raise HTTPException(404, f"session {session_id} not found")
            link = hist.link_session(root, db, Session.model_validate(stored))
        return link.model_dump()

    @r.get("/sessions/{session_id}/file")
    def session_file_provenance(session_id: str, path: str):
        result = hist.file_provenance(root, db, session_id, path)
        if not result.get("found"):
            raise HTTPException(404, f"session {session_id} not found")
        return result

    @r.get("/markers")
    def markers(kind: str | None = None):
        return [m.model_dump() for m in hist.list_markers(db, repo_id, kind)]

    @r.post("/markers")
    def add_marker(body: dict):
        commit = body.get("commit")
        resolved = (git.git(root, "rev-parse", commit, check=False).strip() or commit) if commit else None
        marker = hist.add_marker(db, repo_id, body["kind"], resolved, body.get("description", body["kind"]),
                                body.get("metadata"))
        return marker.model_dump()

    @r.get("/diagnoses")
    def diagnoses(limit: int = 20):
        rows = sorted(db.list("diagnoses", repo_id), key=lambda row: row["created_at"], reverse=True)
        return rows[:limit]

    @r.get("/diagnose/{diagnosis_id}")
    def diagnosis_detail(diagnosis_id: str):
        result = get_diagnosis(db, repo_id, diagnosis_id)
        if result is None:
            raise HTTPException(404, f"diagnosis {diagnosis_id} not found")
        return result.model_dump()

    @r.post("/diagnose")
    def diagnose_endpoint(body: dict):
        result = run_diagnose_(
            root, db, settings, good=body.get("good"), bad=body.get("bad"), since=body.get("since"),
            until=body.get("until"), query=body.get("query"), suspected_path=body.get("file"),
            suspected_symbol=body.get("symbol"), suspected_dependency=body.get("dependency"),
            incident_description=body.get("incident"),
        )
        return result.model_dump()

    return r
