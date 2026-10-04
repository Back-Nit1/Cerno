import json

from conftest import FakeClient, block
from fastapi.testclient import TestClient

from cerno.api.app import create_app
from cerno.llm.openai import LLM
from cerno.models import IntentContract
from cerno.pipeline import build_context
from cerno.verification.agent import verify_with_agent


def _context(demo):
    contract = IntentContract.model_validate(demo.db.get("sessions", demo.session_id, "__contract__"))
    settings = demo.settings.model_copy(update={"max_tool_steps": 3})
    ctx = build_context(demo.root, demo.session_id, settings, demo.db, contract)
    req = next(r for r in contract.requirements if "login/session" in r.text)
    return ctx, req


def _llm(demo, responses):
    client = FakeClient(responses)
    return LLM(demo.settings.model_copy(update={"max_tool_steps": 3}), client=client), client


def test_demo_deterministic_results(demo):
    verdicts = {v["requirement_id"]: v for v in demo.db.list("verdicts", demo.session_id)}
    reqs = {r["text"]: r["id"] for r in demo.db.list("requirements", demo.session_id)}
    assert verdicts[reqs["Add tests"]]["status"] == "VERIFIED"
    changed = {c["path"] for c in demo.db.list("file_changes", demo.session_id)}
    assert "README.md" not in changed  # developer's pre-existing dirty work is not attributed to Claude
    assert {"src/auth/session.js", "src/auth/reset.js", "test/reset.test.js"} <= changed
    flagged = {a["path"]: a["classification"] for a in demo.db.list("change_assessments", demo.session_id)}
    assert flagged["src/auth/session.js"] in {"UNEXPLAINED", "CONTRADICTORY"}
    assert flagged["src/auth/reset.js"] == "EXPECTED"
    claims = {c["category"]: c["result"] for c in demo.db.list("agent_claims", demo.session_id)}
    assert claims["tests_pass"] == "CONFIRMED"


def test_agent_tool_loop_then_verdict(demo):
    ctx, req = _context(demo)
    first = [block("function_call", call_id="t1", name="read_symbol",
                   arguments=json.dumps({"symbol_id": "src/auth/session.js::SESSION_TTL_MS", "version": "baseline"}))]
    verdict_holder = {}

    def second_turn(**kwargs):  # the model reads the tool result and cites its evidence id
        result = kwargs["input"][-1]["output"]
        verdict_holder["ev"] = ev_id = result.split("[evidence ")[1].split("]")[0]
        return [block("function_call", call_id="t2", name="submit_verdict", arguments=json.dumps({
            "status": "VIOLATED", "evidence_level": "semantic", "summary": "SESSION_TTL_MS went from 24h to 30m.",
            "evidence_ids": [ev_id], "missing_evidence": []}))]

    llm, client = _llm(demo, [first, second_turn])
    verdict = verify_with_agent(llm, ctx, req, "PACKET", [], ["agent"])
    assert verdict.status == "VIOLATED" and verdict.evidence_ids == [verdict_holder["ev"]]
    assert verdict.tool_calls_used == 1 and verdict.trace[0].tool == "read_symbol"
    assert "24 * 60 * 60 * 1000" in ctx.store.raw[verdict_holder["ev"]]
    assert llm.calls == 2 and not any(t["name"] == "bash" for t in client.requests[0]["tools"])

    # Identical inputs: served from cache, no new model call, evidence citations still resolvable.
    llm2, client2 = _llm(demo, [])
    ctx2, _ = _context(demo)
    cached = verify_with_agent(llm2, ctx2, req, "PACKET", [], ["agent"])
    assert cached.cached and cached.status == "VIOLATED" and llm2.calls == 0
    assert ctx2.store.get(cached.evidence_ids[0]) is not None


def test_agent_budget_exhaustion_returns_uncertain(demo):
    ctx, req = _context(demo)
    loop = [block("function_call", call_id=f"t{i}", name="get_changed_files", arguments="{}") for i in range(1)]
    llm, client = _llm(demo, [loop, loop, loop])
    verdict = verify_with_agent(llm, ctx, req, "PACKET-budget", [], ["agent"])
    assert verdict.status == "UNCERTAIN" and llm.calls == 3
    assert client.requests[-1]["tool_choice"] == {"type": "function", "name": "submit_verdict"}


def test_agent_malformed_verdict_is_rejected(demo):
    ctx, req = _context(demo)
    bad = [block("function_call", call_id="t1", name="submit_verdict",
                 arguments=json.dumps({"status": "APPROVED", "summary": "lgtm"}))]
    text_only = [block("message", content=[])]
    llm, _ = _llm(demo, [bad, text_only, text_only])
    verdict = verify_with_agent(llm, ctx, req, "PACKET-malformed", [], ["agent"])
    assert verdict.status == "UNCERTAIN"


def test_agent_pure_opinion_cannot_verify(demo):
    ctx, req = _context(demo)
    ev = ctx.store.add("code", "final", "some code", raw="x").id
    opinion = [block("function_call", call_id="t1", name="submit_verdict", arguments=json.dumps({
        "status": "VERIFIED", "evidence_level": "runtime", "summary": "Looks right.", "evidence_ids": [ev],
        "missing_evidence": []}))]
    llm, _ = _llm(demo, [opinion])
    assert verify_with_agent(llm, ctx, req, "PACKET-opinion", [], ["agent"]).status == "LIKELY"


def test_api_endpoints(demo):
    client = TestClient(create_app(demo.root), base_url="http://127.0.0.1")
    sid = demo.session_id
    assert client.get("/api/health").json()["status"] == "ok"
    sessions = client.get("/api/sessions").json()
    assert sessions[0]["id"] == sid and sessions[0]["status"] == "complete" and sessions[0]["verified_count"] >= 1
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["intent"]["requirements"] and detail["verdicts"] and detail["project_checks"][0]["command"]
    assert {"reason", "related_requirement_id"} <= detail["unexpected_changes"][0].keys()
    assert "files_in_repository" in detail["usage"]
    reqs = client.get(f"/api/sessions/{sid}/requirements").json()
    assert all("verdict" in r for r in reqs)
    trace = client.get(f"/api/sessions/{sid}/requirements/{reqs[-1]['id']}").json()
    assert trace["evidence"]
    assert client.get(f"/api/sessions/{sid}/changes").json()["files"]
    assert client.get(f"/api/sessions/{sid}/evidence").json()
    assert client.get(f"/api/sessions/{sid}/usage").json()["requirements_checked"] == len(reqs)
    assert client.get("/api/sessions/nope").status_code == 404
    stream = client.get(f"/api/sessions/{sid}/stream")
    assert stream.text.startswith("data: ")
