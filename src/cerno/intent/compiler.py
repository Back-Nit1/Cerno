"""Intent Compiler: developer prompt(s) -> Intent Contract, via a forced strict tool call to the model.

Without a model, a transparent sentence-level fallback is used and labeled as such.
"""

import hashlib
import re

from pydantic import ValidationError

from cerno.llm.anthropic import LLM
from cerno.models import IntentContract, Requirement
from cerno.privacy import redact
from cerno.storage.sqlite import DB

MAX_REQUIREMENTS = 10

SYSTEM = """You compile a developer's request to a coding agent into an Intent Contract.
Rules:
- Extract only requirements the developer stated or that are directly and unavoidably implied.
- Prefer a small set (usually 2-8) of meaningful, independently testable statements. Never pad.
- type: feature (new capability), invariant (existing behavior that must keep working), constraint (how it must be done),
  prohibition (something that must not happen), security, performance, verification (tests/builds), other.
- explicit=true only if the developer literally asked for it.
- verification_hint: one short phrase on what evidence would prove it, or "" if unclear.
- protected_areas: files, directories, or subsystems the developer said must not change (may be empty).
Call submit_intent_contract exactly once."""

_REQ_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "text", "type", "priority", "explicit", "verification_hint"],
    "properties": {
        "id": {"type": "string"},
        "text": {"type": "string"},
        "type": {"type": "string", "enum": [
            "feature", "invariant", "constraint", "prohibition", "security", "performance", "verification", "other"
        ]},
        "priority": {"type": "string", "enum": ["critical", "high", "normal"]},
        "explicit": {"type": "boolean"},
        "verification_hint": {"type": "string"},
    },
}
INTENT_TOOL = {
    "name": "submit_intent_contract",
    "description": "Submit the compiled Intent Contract.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_summary", "requirements", "protected_areas", "explicit_acceptance_criteria"],
        "properties": {
            "task_summary": {"type": "string"},
            "requirements": {"type": "array", "items": _REQ_SCHEMA},
            "protected_areas": {"type": "array", "items": {"type": "string"}},
            "explicit_acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def parse_contract(data: dict) -> IntentContract:
    """Validate untrusted model output and normalize ids/limits."""
    contract = IntentContract.model_validate(data)
    reqs = []
    for i, req in enumerate(contract.requirements[:MAX_REQUIREMENTS], start=1):
        hint = (req.verification_hint or "").strip() or None
        reqs.append(req.model_copy(update={"id": f"R{i}", "verification_hint": hint}))
    if not reqs:
        raise ValueError("contract has no requirements")
    return contract.model_copy(update={"requirements": reqs})


def compile_intent(llm: LLM, prompts: list[str], db: DB | None = None) -> tuple[IntentContract, str]:
    text = "\n\n".join(p.strip() for p in prompts if p.strip())
    if not text:
        return IntentContract(task_summary="(no developer prompt captured)", requirements=[]), "none"
    key = f"intent:{llm.model}:{hashlib.sha1(text.encode()).hexdigest()}"
    if db is not None and (hit := db.cache_get(key)):
        return IntentContract.model_validate(hit), "model"
    if llm.available:
        try:
            response = llm.create(
                "intent", system=SYSTEM, max_tokens=2048, tools=[INTENT_TOOL],
                tool_choice={"type": "tool", "name": "submit_intent_contract"},
                messages=[{"role": "user", "content": f"<developer_request>\n{redact(text)}\n</developer_request>"}],
            )
            block = next(b for b in response.content if b.type == "tool_use")
            contract = parse_contract(block.input)
            if db is not None:
                db.cache_put(key, contract.model_dump())
            return contract, "model"
        except (StopIteration, ValidationError, ValueError, TypeError):
            pass  # malformed model output: fall through to the labeled fallback
    return fallback_contract(text), "fallback"


RULES = [
    ("prohibition", "critical", r"\b(do not|don't|dont|never|must not|should not|without|avoid|no new)\b"),
    ("invariant", "critical", r"\b(preserve|keep existing|continue to|still work|backward|existing .* (work|behavio))"),
    ("verification", "high", r"\b(tests?|build|lint|typecheck)\b"),
    ("security", "critical", r"\b(secrets?|credentials?|api keys?|environment variables?|env vars?)\b"),
    ("constraint", "high", r"\b(must|should|use|only|expire|within|at most|at least)\b"),
]


def fallback_contract(text: str) -> IntentContract:
    """Deterministic sentence split. No invented requirements: one sentence -> at most one requirement."""
    sentences = [s.strip(" -*•\t") for s in re.split(r"(?<=[.!?])\s+|\n+", text)]
    sentences = [s for s in sentences if len(s.split()) >= 2][:MAX_REQUIREMENTS]
    reqs = []
    for i, sentence in enumerate(sentences, start=1):
        lowered = sentence.lower()
        kind, priority = next(
            ((k, p) for k, p, pattern in RULES if re.search(pattern, lowered)),
            ("feature", "critical" if i == 1 else "high"),
        )
        reqs.append(Requirement(id=f"R{i}", text=sentence.rstrip("."), type=kind, priority=priority, explicit=True))
    summary = sentences[0].rstrip(".") if sentences else text[:80]
    return IntentContract(task_summary=summary, requirements=reqs)
