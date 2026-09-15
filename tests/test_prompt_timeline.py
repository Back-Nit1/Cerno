import json
import sys

from conftest import FakeClient, block, run_git, write
from fastapi.testclient import TestClient

from cerno.api.app import create_app
from cerno.capture.hooks import handle_hook
from cerno.capture.snapshots import now, take_snapshot
from cerno.config import Settings, db_file
from cerno.intent.compiler import compile_intent_timeline, fallback_timeline
from cerno.llm.openai import LLM
from cerno.models import PromptRecord, Session
from cerno.pipeline import analyze
from cerno.storage.sqlite import DB


def _prompt(session_id: str, seq: int, text: str) -> PromptRecord:
    return PromptRecord(id=f"{session_id}-P{seq}", session_id=session_id, seq=seq, timestamp=f"t{seq}", text=text)


# --- Deterministic (no-OpenAI) revision-aware classification -----------------------------------


def test_refinement_preserves_earlier_requirement():
    prompts = [_prompt("s", 1, "Add password reset."),
               _prompt("s", 2, "Also make reset tokens expire after 30 minutes.")]
    contract, classified = fallback_timeline(prompts)
    assert classified[1].classification == "refinement"
    assert classified[1].refines == ["s-P1"]
    assert all(r.active for r in contract.requirements)
    origin_of_expiry = next(r for r in contract.requirements if "expire" in r.text.lower())
    assert origin_of_expiry.origin_prompt_id == "s-P2"


def test_correction_supersedes_one_requirement():
    prompts = [_prompt("s", 1, "Use JWT reset tokens."),
               _prompt("s", 2, "Actually, do not use JWT. Store opaque reset tokens in the database instead.")]
    contract, classified = fallback_timeline(prompts)
    assert classified[1].classification == "correction"
    jwt_req = next(r for r in contract.requirements if r.text == "Use JWT reset tokens")
    assert jwt_req.active is False
    assert jwt_req.superseded_by == "s-P2"
    assert any(r.active and "opaque" in r.text.lower() for r in contract.requirements)


def test_replacement_supersedes_earlier_task():
    prompts = [_prompt("s", 1, "Add a CSV export button to the dashboard."),
               _prompt("s", 2, "Forget that, start over: build a PDF export button instead.")]
    contract, classified = fallback_timeline(prompts)
    assert classified[1].classification == "replacement"
    csv_req = next(r for r in contract.requirements if "CSV" in r.text)
    assert csv_req.active is False and csv_req.superseded_by == "s-P2"


def test_continuation_of_same_task():
    prompts = [_prompt("s", 1, "Refactor the auth module to use dependency injection."),
               _prompt("s", 2, "Continue, keep going with the same refactor.")]
    contract, classified = fallback_timeline(prompts)
    assert classified[1].classification == "continuation"
    assert classified[1].refines == ["s-P1"]
    assert all(r.active for r in contract.requirements)


def test_unrelated_tasks_within_one_session():
    prompts = [_prompt("s", 1, "Add a login page."),
               _prompt("s", 2, "Fix the invoice PDF export crash, unrelated to that.")]
    contract, classified = fallback_timeline(prompts)
    assert classified[1].classification == "new_task"
    assert classified[1].refines == [] and classified[1].supersedes == []
    assert all(r.active for r in contract.requirements)


def test_ambiguous_evolution_without_openai():
    prompts = [_prompt("s", 1, "Improve the session handling in the auth service."),
               _prompt("s", 2, "Update the session timeout value.")]
    contract, classified = fallback_timeline(prompts)
    assert classified[1].classification == "ambiguous"
    assert classified[1].ambiguous is True
    assert contract.ambiguities  # flagged for developer review, never silently reconciled


# --- Model-path (OpenAI) revision-aware compilation, via a scripted fake client ----------------


def test_compile_intent_timeline_uses_model_provenance():
    prompts = [_prompt("s", 1, "Use JWT reset tokens."),
               _prompt("s", 2, "Actually, do not use JWT. Store opaque reset tokens instead.")]
    data = {
        "task_summary": "Password reset with opaque tokens",
        "protected_areas": [], "explicit_acceptance_criteria": [],
        "requirements": [
            {"id": "x", "text": "Use JWT reset tokens", "type": "constraint", "priority": "high", "explicit": True,
             "verification_hint": "", "origin_seq": 1, "refined_by_seqs": [], "superseded_by_seq": 2,
             "active": False},
            {"id": "y", "text": "Store opaque reset tokens", "type": "constraint", "priority": "high",
             "explicit": True, "verification_hint": "", "origin_seq": 2, "refined_by_seqs": [],
             "superseded_by_seq": None, "active": True},
        ],
        "prompts": [
            {"seq": 1, "classification": "new_task", "refines_seqs": [], "supersedes_seqs": [], "confidence": 1.0,
             "ambiguous": False},
            {"seq": 2, "classification": "correction", "refines_seqs": [], "supersedes_seqs": [1], "confidence": 0.9,
             "ambiguous": False},
        ],
        "ambiguities": [],
    }
    client = FakeClient([[block("function_call", call_id="t", name="submit_prompt_timeline",
                                arguments=json.dumps(data))]])
    contract, classified, source = compile_intent_timeline(LLM(Settings(), client=client), prompts)
    assert source == "model"
    assert classified[1].classification == "correction" and classified[1].supersedes == ["s-P1"]
    jwt_req = next(r for r in contract.requirements if r.text == "Use JWT reset tokens")
    assert jwt_req.active is False and jwt_req.superseded_by == "s-P2" and jwt_req.origin_prompt_id == "s-P1"


def test_compile_intent_timeline_falls_back_on_malformed_model_output():
    prompts = [_prompt("s", 1, "Add password reset.")]
    client = FakeClient([[block("function_call", call_id="t", name="submit_prompt_timeline",
                                arguments=json.dumps({"task_summary": "t", "requirements": []}))]])
    contract, classified, source = compile_intent_timeline(LLM(Settings(), client=client), prompts)
    assert source == "fallback"
    assert contract.requirements and classified[0].classification == "new_task"


# --- Git checkpoints across prompts: commits, dirty state, symbol attribution ------------------


def test_git_commit_between_prompts_does_not_break_attribution(repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    base = {"session_id": "commit1", "cwd": str(repo)}
    handle_hook("user-prompt", {**base, "prompt": "Add a greet helper function please"})
    write(repo, {"greet.js": "export function greet() { return 'hi'; }\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "wip: greet helper")  # developer commits mid-session
    handle_hook("user-prompt", {**base, "prompt": "Also add a farewell helper function please"})
    write(repo, {"farewell.js": "export function farewell() { return 'bye'; }\n"})
    handle_hook("stop", base)

    settings = Settings.load(repo)
    analyze(repo, "commit1", settings)
    db = DB(db_file(repo, settings))
    segments = {s["id"]: s for s in db.list("diff_segments", "commit1")}
    prompts = db.list("prompt_timeline", "commit1")
    assert prompts[0]["head"] != prompts[1]["head"]  # HEAD moved mid-session
    assert [f["path"] for f in segments["prompt-1->prompt-2"]["files"]] == ["greet.js"]
    assert [f["path"] for f in segments["prompt-2->final"]["files"]] == ["farewell.js"]


def test_dirty_changes_between_prompts_are_attributed_to_correct_segment(repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    base = {"session_id": "dirty1", "cwd": str(repo)}
    handle_hook("user-prompt", {**base, "prompt": "Add a greet helper function please"})
    write(repo, {"greet.js": "export function greet() { return 'hi'; }\n"})  # left dirty, never committed
    handle_hook("user-prompt", {**base, "prompt": "Also add a farewell helper function please"})
    write(repo, {"farewell.js": "export function farewell() { return 'bye'; }\n"})
    handle_hook("stop", base)

    settings = Settings.load(repo)
    analyze(repo, "dirty1", settings)
    db = DB(db_file(repo, settings))
    segments = {s["id"]: s for s in db.list("diff_segments", "dirty1")}
    assert [f["path"] for f in segments["prompt-1->prompt-2"]["files"]] == ["greet.js"]
    assert [f["path"] for f in segments["prompt-2->final"]["files"]] == ["farewell.js"]


def test_file_and_symbol_attribution_by_segment(repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    write(repo, {"src/util.js": "export function a() { return 1; }\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "seed util")
    base = {"session_id": "sym1", "cwd": str(repo)}
    handle_hook("user-prompt", {**base, "prompt": "Add a function b that returns 2"})
    write(repo, {"src/util.js": "export function a() { return 1; }\nexport function b() { return 2; }\n"})
    handle_hook("post-tool", {**base, "tool_name": "Edit", "tool_input": {"file_path": str(repo / "src/util.js")},
                              "tool_response": {"success": True}})
    handle_hook("stop", base)

    settings = Settings.load(repo)
    analyze(repo, "sym1", settings)
    db = DB(db_file(repo, settings))
    attrs = {a["path"]: a for a in db.list("attributions", "sym1")}
    assert attrs["src/util.js"]["prompt_id"] == "sym1-P1"
    seg = next(s for s in db.list("diff_segments", "sym1") if s["id"] == "prompt-1->final")
    assert any(sym["symbol_name"] == "b" for sym in seg["symbols"])


def test_tool_event_supported_attribution_is_direct_otherwise_temporal(repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    base = {"session_id": "attr1", "cwd": str(repo)}
    handle_hook("user-prompt", {**base, "prompt": "Add an edited file and an untracked output file"})
    write(repo, {"edited.js": "export const x = 1;\n", "output.txt": "hello\n"})
    # Only edited.js gets a recorded PostToolUse event; output.txt changed with no captured tool event.
    handle_hook("post-tool", {**base, "tool_name": "Write", "tool_input": {"file_path": str(repo / "edited.js")},
                              "tool_response": {"success": True}})
    handle_hook("stop", base)

    settings = Settings.load(repo)
    analyze(repo, "attr1", settings)
    db = DB(db_file(repo, settings))
    attrs = {a["path"]: a for a in db.list("attributions", "attr1")}
    assert attrs["edited.js"]["confidence"] == "direct"
    assert attrs["output.txt"]["confidence"] == "temporal"
    assert "not proven causality" in attrs["output.txt"]["basis"]


# --- Backward compatibility, API serialization, verdict-count exclusion ------------------------


def test_backward_compatible_with_prompts_only_session(repo):
    session_id = "legacy1"
    session = Session(id=session_id, repo_root=str(repo), prompts=["Add a health check endpoint."],
                      created_at=now(), updated_at=now())
    db = DB(db_file(repo, Settings.load(repo)))
    db.put("sessions", session_id, session_id, session)
    take_snapshot(repo, session_id, "baseline")
    write(repo, {"health.js": "export function health() { return 'ok'; }\n"})
    take_snapshot(repo, session_id, "final")

    settings = Settings.load(repo)
    result = analyze(repo, session_id, settings)
    assert result.status == "analyzed"
    prompts = db.list("prompt_timeline", session_id)
    assert len(prompts) == 1 and prompts[0]["seq"] == 1
    assert prompts[0]["head"] is None and prompts[0]["tree"] is None  # synthesized: no real checkpoint existed


def test_superseded_requirements_excluded_from_verdict_counts(repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    base = {"session_id": "verdict1", "cwd": str(repo)}
    handle_hook("user-prompt", {**base, "prompt": "Use JWT reset tokens."})
    write(repo, {"src/auth.js": "export function resetJwt() { return 'jwt'; }\n"})
    handle_hook("post-tool", {**base, "tool_name": "Write", "tool_input": {"file_path": str(repo / "src/auth.js")},
                              "tool_response": {"success": True}})
    handle_hook("user-prompt",
               {**base, "prompt": "Actually, do not use JWT. Store opaque reset tokens in the database instead."})
    write(repo, {"src/auth.js": "export function resetOpaque() { return 'opaque'; }\n"})
    handle_hook("post-tool", {**base, "tool_name": "Edit", "tool_input": {"file_path": str(repo / "src/auth.js")},
                              "tool_response": {"success": True}})
    handle_hook("stop", base)

    settings = Settings.load(repo)
    session = analyze(repo, "verdict1", settings)
    db = DB(db_file(repo, settings))
    reqs = {r["id"]: r for r in db.list("requirements", "verdict1")}
    verdicts = db.list("verdicts", "verdict1")
    superseded_ids = {rid for rid, r in reqs.items() if not r["active"]}
    assert superseded_ids  # sanity: the JWT requirement was in fact superseded
    assert not (superseded_ids & {v["requirement_id"] for v in verdicts})
    assert sum(session.counts.values()) == len(verdicts) == len(reqs) - len(superseded_ids)


def test_api_timeline_and_segments_endpoints(demo):
    client = TestClient(create_app(demo.root))
    sid = demo.session_id
    timeline = client.get(f"/api/sessions/{sid}/timeline").json()
    assert timeline and timeline[0]["seq"] == 1
    assert {"files_changed", "symbols_changed", "requirements_introduced", "requirements_refined",
            "requirements_superseded", "classification"} <= timeline[0].keys()
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["prompt_timeline"] == timeline
    segments = client.get(f"/api/sessions/{sid}/segments").json()
    assert segments and all("prompt_id" in s for s in segments)


# --- Proof hooks make no model calls ------------------------------------------------------------


def test_hooks_never_import_openai_sdk(repo, monkeypatch):
    monkeypatch.delitem(sys.modules, "openai", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-be-used")
    base = {"session_id": "nomodel1", "cwd": str(repo)}
    handle_hook("session-start", base)
    handle_hook("user-prompt", {**base, "prompt": "Add a health check endpoint please"})
    write(repo, {"health.js": "export function health() { return 'ok'; }\n"})
    handle_hook("post-tool", {**base, "tool_name": "Write", "tool_input": {"file_path": str(repo / "health.js")},
                              "tool_response": {"success": True}})
    handle_hook("user-prompt", {**base, "prompt": "Also add a version endpoint please"})
    handle_hook("stop", base)
    assert "openai" not in sys.modules, "a hook path imported the OpenAI SDK: hooks must never call the model"
