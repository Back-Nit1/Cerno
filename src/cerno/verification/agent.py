"""Bounded tool-using verification agent (Anthropic SDK directly, no framework)."""

import hashlib
import json

from pydantic import ValidationError

from cerno.llm.anthropic import LLM, block_to_param
from cerno.models import Requirement, TraceStep, Verdict
from cerno.verification.context import Context
from cerno.verification.tools import TOOLS, execute
from cerno.verification.verdict import finalize, uncertain

SYSTEM_PROMPT = """You are Cerno, an evidence-first software verification agent.

Your job is not to approve code by default.
Your job is to determine whether one developer requirement is satisfied.

Investigate before concluding.

Prefer deterministic and runtime evidence over semantic inference.
Request only the smallest additional evidence needed.
Never assume a test passes unless tool output proves it.
Never assume unchanged behavior merely because existing tests pass.
Do not inspect unrelated code.
Do not call tools once the evidence is sufficient.
Return UNCERTAIN when the available evidence cannot establish a conclusion.
Every conclusion must cite evidence IDs supplied by tools.

Status rules:
- VERIFIED: only when test, runtime, or deterministic rule evidence directly establishes the requirement. Reading code is never enough.
- LIKELY: code/structure strongly supports the requirement but nothing conclusive proves it.
- VIOLATED: evidence directly contradicts the requirement (a clear code change counts, e.g. a changed constant that alters existing behavior).
- UNCERTAIN: incomplete or conflicting evidence.

Finish by calling submit_verdict once. summary: 1-3 concrete sentences naming files, symbols, and values.
missing_evidence: what would settle any remaining doubt (empty if none)."""

RESULT_KEEP_TURNS = 2


def _tool_result(tool_use_id: str, text: str, is_error: bool = False) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": text, "is_error": is_error}


def _compact(messages: list[dict]) -> None:
    """Elide older tool outputs so history does not grow with every step; evidence stays retrievable by id."""
    result_turns = [m for m in messages if m["role"] == "user" and isinstance(m["content"], list)]
    for message in result_turns[:-RESULT_KEEP_TURNS]:
        for block in message["content"]:
            text = block.get("content")
            if isinstance(text, str) and not text.endswith("(elided)"):
                block["content"] = text.splitlines()[0][:200] + " ... (elided)"


def _cache_key(llm: LLM, ctx: Context, req: Requirement, packet: str) -> str:
    checks = sorted((k, c.exit_code) for k, c in ctx.checks.items())
    material = [llm.model, req.model_dump(), ctx.base_tree, ctx.final_tree, packet, checks, SYSTEM_PROMPT,
                ctx.settings.max_tool_steps]
    return "verdict:" + hashlib.sha1(json.dumps(material, sort_keys=True).encode()).hexdigest()


def verify_with_agent(llm: LLM, ctx: Context, req: Requirement, packet: str, seed_ids: list[str],
                      plan: list[str]) -> Verdict:
    plan_text = " -> ".join(plan)
    if not llm.available:
        return uncertain(
            req.id, "Semantic verification unavailable (ANTHROPIC_API_KEY not set); deterministic evidence alone "
            "does not settle this requirement.", ["model reasoning over the evidence packet"], seed_ids, plan=plan_text,
        )
    key = _cache_key(llm, ctx, req, packet)
    if hit := ctx.db.cache_get(key):
        ctx.store.restore(hit["evidence"])
        return Verdict.model_validate(hit["verdict"]).model_copy(update={"cached": True})

    ctx.requirement_files, ctx.extra_expansion_used = set(), False
    calls_before, max_steps = ctx.tool_calls, ctx.settings.max_tool_steps
    trace: list[TraceStep] = []
    messages: list[dict] = [{
        "role": "user",
        "content": ctx.send(packet) + f"\n\nBudget: {max_steps} turns. Call submit_verdict as soon as evidence suffices.",
    }]
    system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    verdict, nudged = None, False

    for step in range(max_steps):
        final_turn = step == max_steps - 1
        response = llm.create(
            "verify", system=system, tools=TOOLS, messages=messages, max_tokens=1500,
            tool_choice={"type": "tool", "name": "submit_verdict"} if final_turn else {"type": "auto"},
        )
        blocks = list(response.content)
        uses = [b for b in blocks if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": [block_to_param(b) for b in blocks]})
        if not uses:
            if nudged:
                break
            nudged = True
            messages.append({"role": "user", "content": "Call submit_verdict with your conclusion and cited evidence ids."})
            continue
        results = []
        for use in uses:
            args = use.input if isinstance(use.input, dict) else {}
            if use.name == "submit_verdict":
                try:
                    verdict = Verdict(requirement_id=req.id, files_inspected=[], tool_calls_used=0, **args)
                    results.append(_tool_result(use.id, "verdict recorded"))
                except (ValidationError, TypeError) as exc:
                    results.append(_tool_result(use.id, f"invalid verdict, resubmit: {str(exc)[:400]}", True))
                continue
            text, ids, is_error = execute(ctx, use.name, args)
            trace.append(TraceStep(tool=use.name, input=args, summary=text.splitlines()[0][:200] if text else "",
                                   evidence_ids=ids))
            results.append(_tool_result(use.id, text, is_error))
        if verdict is not None:
            break
        messages.append({"role": "user", "content": results})
        _compact(messages)

    if verdict is None:
        verdict = uncertain(req.id, "Investigation ended without a valid verdict (step budget exhausted or malformed "
                            "model output).", ["conclusive evidence within the investigation budget"], seed_ids)
    verdict = finalize(verdict.model_copy(update={
        "files_inspected": sorted(ctx.requirement_files), "tool_calls_used": ctx.tool_calls - calls_before,
        "trace": trace, "plan": plan_text,
    }), ctx.store)
    cited = list(dict.fromkeys(verdict.evidence_ids + seed_ids + [i for s in trace for i in s.evidence_ids]))
    ctx.db.cache_put(key, {"verdict": verdict.model_dump(), "evidence": ctx.store.dump(cited)})
    return verdict
