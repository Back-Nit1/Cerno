"""FastAPI backend. Serializes stored state into the dashboard wire contract (ui/src/types.ts); no logic in the UI."""

import asyncio
import json
import os
import threading
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from cerno.analysis import git
from cerno.capture.snapshots import load_snapshot
from cerno.config import Settings, db_file
from cerno.models import Session
from cerno.storage.sqlite import DB

UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"
UI_STATUS = {"analyzing": "analyzing", "analyzed": "complete", "failed": "failed"}


def create_app(root: Path | None = None) -> FastAPI:
    root = root or Path(os.environ.get("CERNO_REPO") or git.get_repository_root(Path.cwd()) or Path.cwd())
    settings = Settings.load(root)
    db = DB(db_file(root, settings))
    running: set[str] = set()
    app = FastAPI(title="Cerno", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    def session(sid: str) -> Session:
        row = db.get("sessions", sid, sid)
        if row is None:
            raise HTTPException(404, f"session {sid} not found")
        return Session.model_validate(row)

    def summary(s: Session) -> dict:
        counts = s.counts or {}
        return {
            "id": s.id, "task_summary": s.task_summary or (s.prompts[0][:100] if s.prompts else "(no prompt captured)"),
            "status": UI_STATUS.get(s.status, "capturing"), "capture_status": s.status, "created_at": s.created_at,
            "updated_at": s.updated_at, "verified_count": counts.get("VERIFIED", 0),
            "likely_count": counts.get("LIKELY", 0), "uncertain_count": counts.get("UNCERTAIN", 0),
            "violated_count": counts.get("VIOLATED", 0),
        }

    def usage(sid: str) -> dict:
        u = db.get("model_usage", sid, "__summary__") or {}
        return {
            "model_input_tokens": u.get("model_input_tokens", 0), "model_output_tokens": u.get("model_output_tokens", 0),
            "model_calls": u.get("model_calls", 0), "tool_calls": u.get("tool_calls", 0),
            "files_in_repository": u.get("repo_files", 0), "files_inspected": u.get("files_inspected", 0),
            "symbols_inspected": u.get("symbols_inspected", 0), "requirements_checked": u.get("requirements_checked", 0),
            "cache_hits": u.get("cache_hits", 0), "cache_read_tokens": u.get("cache_read_tokens", 0),
            "repository_source_lines": u.get("repo_lines"), "lines_sent_to_model": u.get("lines_sent_to_model", 0),
            "context_avoided_pct": u.get("context_avoided_pct"),
        }

    def checks(sid: str) -> list[dict]:
        return [{**c, "command": " ".join(c["command"]), "duration_ms": int(c["duration_s"] * 1000)}
                for c in db.list("project_checks", sid)]

    def unexpected(sid: str) -> list[dict]:
        return [{**a, "reason": f"{a['what_changed']}. {a['why_impact']}",
                 "related_requirement_id": (a["affected_requirements"] or [None])[0]}
                for a in db.list("change_assessments", sid)]

    def timeline(sid: str) -> list[dict]:
        prompts = sorted(db.list("prompt_timeline", sid), key=lambda p: p["seq"])
        reqs = db.list("requirements", sid)
        files_by_prompt: dict[str, set[str]] = {}
        for a in db.list("attributions", sid):
            if a["prompt_id"]:
                files_by_prompt.setdefault(a["prompt_id"], set()).add(a["path"])
        symbols_by_prompt: dict[str, set[str]] = {}
        for seg in db.list("diff_segments", sid):
            if seg["prompt_id"]:
                symbols_by_prompt.setdefault(seg["prompt_id"], set()).update(
                    f"{s['file_path']}::{s['symbol_name']}" for s in seg["symbols"])
        out = []
        for p in prompts:
            out.append({
                **p,
                "files_changed": sorted(files_by_prompt.get(p["id"], ())),
                "symbols_changed": sorted(symbols_by_prompt.get(p["id"], ())),
                "requirements_introduced": [r["id"] for r in reqs if r.get("origin_prompt_id") == p["id"]],
                "requirements_refined": [r["id"] for r in reqs if p["id"] in (r.get("refined_by") or [])],
                "requirements_superseded": [r["id"] for r in reqs if r.get("superseded_by") == p["id"]],
            })
        return out

    @app.get("/api/health")
    def health():
        return {"status": "ok", "repository_root": str(root), "model": settings.model,
                "model_available": bool(settings.api_key)}

    @app.get("/api/sessions")
    def sessions():
        rows = [Session.model_validate(r) for r in db.list("sessions") if "prompts" in r]
        return [summary(s) for s in sorted(rows, key=lambda s: s.created_at, reverse=True)]

    @app.get("/api/sessions/{sid}")
    def session_detail(sid: str):
        s = session(sid)
        contract = db.get("sessions", sid, "__contract__") or {
            "task_summary": s.task_summary or "", "requirements": [], "protected_areas": [],
            "explicit_acceptance_criteria": [],
        }
        baseline = load_snapshot(root, sid, "baseline")
        return {
            **summary(s), "repository_root": s.repo_root, "head_commit": (baseline.head if baseline else None) or "",
            "prompts": s.prompts, "intent_source": s.intent_source, "error": s.error, "progress": s.progress,
            "intent": contract, "verdicts": db.list("verdicts", sid), "file_changes": db.list("file_changes", sid),
            "symbol_changes": db.list("symbol_changes", sid), "unexpected_changes": unexpected(sid),
            "project_checks": checks(sid), "agent_claims": db.list("agent_claims", sid), "usage": usage(sid),
            "prompt_timeline": timeline(sid),
        }

    @app.get("/api/sessions/{sid}/requirements")
    def requirements(sid: str):
        session(sid)
        verdicts = {v["requirement_id"]: v for v in db.list("verdicts", sid)}
        return [{**r, "verdict": verdicts.get(r["id"])} for r in db.list("requirements", sid)]

    @app.get("/api/sessions/{sid}/requirements/{rid}")
    def requirement_trace(sid: str, rid: str):
        req = db.get("requirements", sid, rid)
        if req is None:
            raise HTTPException(404, f"requirement {rid} not found")
        verdict = db.get("verdicts", sid, rid)
        ids = set(verdict["evidence_ids"]) | {i for st in verdict.get("trace", []) for i in st["evidence_ids"]} if verdict else set()
        evidence = [e for e in db.list("evidence", sid) if e["id"] in ids]
        return {"requirement": req, "verdict": verdict, "evidence": evidence}

    @app.get("/api/sessions/{sid}/timeline")
    def timeline_endpoint(sid: str):
        session(sid)
        return timeline(sid)

    @app.get("/api/sessions/{sid}/segments")
    def segments_endpoint(sid: str):
        session(sid)
        return db.list("diff_segments", sid)

    @app.get("/api/sessions/{sid}/changes")
    def changes(sid: str):
        session(sid)
        return {"files": db.list("file_changes", sid), "symbols": db.list("symbol_changes", sid),
                "unexpected": unexpected(sid)}

    @app.get("/api/sessions/{sid}/evidence")
    def evidence(sid: str):
        session(sid)
        return [{**e, "raw": (e.get("raw") or "")[:4000]} for e in db.list("evidence", sid)]

    @app.get("/api/sessions/{sid}/evidence/{eid}")
    def evidence_item(sid: str, eid: str):
        item = db.get("evidence", sid, eid)
        if item is None:
            raise HTTPException(404, f"evidence {eid} not found")
        return item

    @app.get("/api/sessions/{sid}/checks")
    def project_checks(sid: str):
        session(sid)
        return checks(sid)

    @app.get("/api/sessions/{sid}/claims")
    def claims(sid: str):
        session(sid)
        return db.list("agent_claims", sid)

    @app.get("/api/sessions/{sid}/events")
    def events(sid: str):
        session(sid)
        return db.list("events", sid)

    @app.get("/api/sessions/{sid}/usage")
    def usage_endpoint(sid: str):
        session(sid)
        return usage(sid)

    @app.post("/api/sessions/{sid}/analyze")
    def analyze(sid: str):
        session(sid)
        if sid in running:
            return {"started": False, "reason": "analysis already running"}
        running.add(sid)

        def work():
            from cerno.pipeline import analyze as run  # heavy imports only when analysis runs

            try:
                run(root, sid, settings)
            except Exception:  # noqa: BLE001 - failure is recorded on the session
                pass
            finally:
                running.discard(sid)

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    @app.get("/api/sessions/{sid}/stream")
    async def stream(sid: str):
        session(sid)

        async def events():
            last = None
            for _ in range(3600):  # ~30 minutes max
                s = session(sid)
                payload = json.dumps({"status": UI_STATUS.get(s.status, "capturing"), "progress": s.progress,
                                      "counts": dict(Counter(s.counts)), "error": s.error})
                if payload != last:
                    yield f"data: {payload}\n\n"
                    last = payload
                if s.status != "analyzing" and sid not in running:
                    break
                await asyncio.sleep(0.5)

        return StreamingResponse(events(), media_type="text/event-stream")

    if UI_DIST.is_dir():
        @app.get("/{path:path}", include_in_schema=False)
        def ui(path: str):
            target = (UI_DIST / path).resolve()
            if path and target.is_file() and UI_DIST in target.parents:
                return FileResponse(target)
            return FileResponse(UI_DIST / "index.html")

    return app
