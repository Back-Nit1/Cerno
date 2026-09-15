"""Intent Compiler: developer prompt(s) -> Intent Contract, via a forced strict tool call to the model.

Without a model, a transparent sentence-level fallback is used and labeled as such.

Revision-aware compilation (`compile_intent_timeline`) additionally classifies each prompt's
relationship to the prompts before it (new_task/refinement/correction/replacement/continuation/
ambiguous) and threads requirement provenance (origin/refinement/supersession) through. Prompt
classification happens here, during `cerno analyze` — never in hooks, which make no model calls.
"""

import hashlib
import re

from pydantic import ValidationError

from cerno.analysis.retrieval import keywords, overlap
from cerno.llm.openai import LLM, call_arguments, function_calls
from cerno.models import IntentContract, PromptRecord, Requirement
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
    "type": "function",
    "name": "submit_intent_contract",
    "description": "Submit the compiled Intent Contract.",
    "strict": True,
    "parameters": {
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
                "intent", instructions=SYSTEM, max_output_tokens=2048, tools=[INTENT_TOOL],
                tool_choice={"type": "function", "name": "submit_intent_contract"},
                input=[{"role": "user", "content": f"<developer_request>\n{redact(text)}\n</developer_request>"}],
            )
            contract = parse_contract(call_arguments(next(iter(function_calls(response)))))
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


# --- Revision-aware, multi-prompt compilation --------------------------------------------------

TIMELINE_SYSTEM = """You compile an ordered timeline of a developer's prompts to a coding agent into a
revision-aware Intent Contract. Each prompt is tagged [Pn].
Rules:
- Extract only requirements a prompt stated or directly and unavoidably implied.
- Prefer a small set (usually 2-10) of meaningful, independently testable statements. Never pad.
- type: feature (new capability), invariant (existing behavior that must keep working), constraint (how it must
  be done), prohibition (something that must not happen), security, performance, verification (tests/builds),
  other.
- explicit=true only if the developer literally asked for it.
- verification_hint: one short phrase on what evidence would prove it, or "" if unclear.
- protected_areas: files, directories, or subsystems the developer said must not change (may be empty).
- For every requirement set origin_seq to the prompt number [Pn] that introduced it. If a LATER prompt
  narrowed, extended, or added detail to it without contradicting it, add that prompt's number to
  refined_by_seqs and keep active=true. If a later prompt explicitly contradicts, replaces, or withdraws it
  (e.g. "actually don't do X, do Y instead"), set superseded_by_seq to that prompt and active=false. A
  superseded requirement is never silently dropped or merged — it stays in the list, marked inactive.
- Never silently reconcile two prompts that conflict without a clear signal of which one wins: if you cannot
  tell whether a later prompt refines, corrects, replaces, continues, or starts a new unrelated task, classify
  it "ambiguous", set confidence low, and add a plain-language note to `ambiguities` describing what a
  developer needs to resolve. Do not guess.
- classification for each prompt (including P1, which is always "new_task" unless it plainly continues
  something outside this timeline): new_task, refinement, correction, replacement, continuation, ambiguous.
  refines_seqs / supersedes_seqs reference the prompt(s) it relates to (usually 0 or 1 entries).
Call submit_prompt_timeline exactly once."""

_PROMPT_CLASSES = ["new_task", "refinement", "correction", "replacement", "continuation", "ambiguous"]

_TIMELINE_REQ_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "text", "type", "priority", "explicit", "verification_hint",
                 "origin_seq", "refined_by_seqs", "superseded_by_seq", "active"],
    "properties": {
        "id": {"type": "string"},
        "text": {"type": "string"},
        "type": {"type": "string", "enum": [
            "feature", "invariant", "constraint", "prohibition", "security", "performance", "verification", "other"
        ]},
        "priority": {"type": "string", "enum": ["critical", "high", "normal"]},
        "explicit": {"type": "boolean"},
        "verification_hint": {"type": "string"},
        "origin_seq": {"type": "integer"},
        "refined_by_seqs": {"type": "array", "items": {"type": "integer"}},
        "superseded_by_seq": {"type": ["integer", "null"]},
        "active": {"type": "boolean"},
    },
}
_TIMELINE_PROMPT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["seq", "classification", "refines_seqs", "supersedes_seqs", "confidence", "ambiguous"],
    "properties": {
        "seq": {"type": "integer"},
        "classification": {"type": "string", "enum": _PROMPT_CLASSES},
        "refines_seqs": {"type": "array", "items": {"type": "integer"}},
        "supersedes_seqs": {"type": "array", "items": {"type": "integer"}},
        "confidence": {"type": "number"},
        "ambiguous": {"type": "boolean"},
    },
}
TIMELINE_TOOL = {
    "type": "function",
    "name": "submit_prompt_timeline",
    "description": "Submit the revision-aware Intent Contract and per-prompt classifications.",
    "strict": True,
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_summary", "requirements", "protected_areas", "explicit_acceptance_criteria",
                     "prompts", "ambiguities"],
        "properties": {
            "task_summary": {"type": "string"},
            "requirements": {"type": "array", "items": _TIMELINE_REQ_SCHEMA},
            "protected_areas": {"type": "array", "items": {"type": "string"}},
            "explicit_acceptance_criteria": {"type": "array", "items": {"type": "string"}},
            "prompts": {"type": "array", "items": _TIMELINE_PROMPT_SCHEMA},
            "ambiguities": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def parse_timeline(data: dict, prompts: list[PromptRecord]) -> tuple[IntentContract, list[PromptRecord]]:
    """Validate untrusted model output against the captured prompt records and normalize ids."""
    by_seq = {p.seq: p for p in prompts}

    def pid(seq) -> str | None:
        p = by_seq.get(seq)
        return p.id if p else None

    reqs = []
    for i, r in enumerate(data.get("requirements", [])[:MAX_REQUIREMENTS], start=1):
        hint = (r.get("verification_hint") or "").strip() or None
        superseded_seq = r.get("superseded_by_seq")
        reqs.append(Requirement(
            id=f"R{i}", text=r["text"], type=r["type"], priority=r["priority"], explicit=r["explicit"],
            verification_hint=hint, origin_prompt_id=pid(r.get("origin_seq")),
            refined_by=[x for s in r.get("refined_by_seqs", []) if (x := pid(s))],
            superseded_by=pid(superseded_seq) if superseded_seq is not None else None,
            active=r.get("active", True),
        ))
    if not reqs:
        raise ValueError("contract has no requirements")
    contract = IntentContract(
        task_summary=data["task_summary"], requirements=reqs,
        protected_areas=data.get("protected_areas", []),
        explicit_acceptance_criteria=data.get("explicit_acceptance_criteria", []),
        ambiguities=data.get("ambiguities", []),
    )

    by_seq_class = {c["seq"]: c for c in data.get("prompts", [])}
    classified = []
    for p in prompts:
        c = by_seq_class.get(p.seq)
        if c is None:  # model omitted a prompt: never guess, mark it for developer review
            classified.append(p.model_copy(update={"classification": "ambiguous", "confidence": 0.0, "ambiguous": True}))
            continue
        classified.append(p.model_copy(update={
            "classification": c["classification"],
            "refines": [x for s in c.get("refines_seqs", []) if (x := pid(s))],
            "supersedes": [x for s in c.get("supersedes_seqs", []) if (x := pid(s))],
            "confidence": c.get("confidence"), "ambiguous": c.get("ambiguous", False),
        }))
    return contract, classified


def compile_intent_timeline(llm: LLM, prompts: list[PromptRecord],
                            db: DB | None = None) -> tuple[IntentContract, list[PromptRecord], str]:
    """Revision-aware compilation over the ordered prompt timeline. Classification and requirement
    provenance/supersession are computed here (during `cerno analyze`), never in hooks."""
    meaningful = [p for p in prompts if p.text.strip()]
    if not meaningful:
        return IntentContract(task_summary="(no developer prompt captured)", requirements=[]), prompts, "none"
    text = "\n\n".join(f"[P{p.seq}] {p.text.strip()}" for p in meaningful)
    key = f"timeline:{llm.model}:{hashlib.sha1(text.encode()).hexdigest()}"
    if db is not None and (hit := db.cache_get(key)):
        contract = IntentContract.model_validate(hit["contract"])
        _, classified = parse_timeline(hit["raw"], prompts)
        return contract, classified, "model"
    if llm.available:
        try:
            response = llm.create(
                "intent_timeline", instructions=TIMELINE_SYSTEM, max_output_tokens=4096, tools=[TIMELINE_TOOL],
                tool_choice={"type": "function", "name": "submit_prompt_timeline"},
                input=[{"role": "user", "content": f"<developer_prompts>\n{redact(text)}\n</developer_prompts>"}],
            )
            data = call_arguments(next(iter(function_calls(response))))
            contract, classified = parse_timeline(data, prompts)
            if db is not None:
                db.cache_put(key, {"contract": contract.model_dump(), "raw": data})
            return contract, classified, "model"
        except (StopIteration, ValidationError, ValueError, TypeError, KeyError):
            pass  # malformed model output: fall through to the labeled, deterministic fallback
    contract, classified = fallback_timeline(prompts)
    return contract, classified, "fallback"


_CORRECTION_CUES = re.compile(
    r"\b(actually|no,|nvm|never\s?mind|scratch that|don'?t use|do not use|stop using|wait,|on second thought)\b",
    re.I,
)
_REPLACEMENT_CUES = re.compile(
    r"\b(start over|from scratch|forget (that|it|the previous)|different approach|new approach|redo (this|that|it)|"
    r"scrap (that|this|it))\b",
    re.I,
)
_CONTINUATION_CUES = re.compile(
    r"\b(continue|keep going|proceed|go ahead|as (before|planned|discussed)|next step|resume|carry on)\b", re.I
)
_REFINEMENT_CUES = re.compile(r"^\s*(also|additionally|and\s|plus\s|one more thing|on top of that|furthermore)", re.I)


def classify_prompt_fallback(prior_keywords: set[str], text: str) -> tuple[str, float, bool]:
    """Conservative, no-model heuristic classifier. Never guesses a confident relationship it can't
    justify with a cue word or keyword overlap; anything else is reported ambiguous."""
    if not prior_keywords:
        return "new_task", 1.0, False
    kw = keywords(text)
    shared = overlap(kw, prior_keywords)
    ratio = len(shared) / len(kw) if kw else 0.0
    if _CORRECTION_CUES.search(text) and shared:
        return "correction", 0.7, False
    if _REPLACEMENT_CUES.search(text):
        return "replacement", 0.7, False
    if _CONTINUATION_CUES.search(text):
        return "continuation", 0.6, False
    if _REFINEMENT_CUES.search(text) and shared:
        return "refinement", 0.6, False
    if ratio > 0.5:
        return "refinement", 0.5, False
    if ratio == 0.0:
        return "new_task", 0.5, False
    return "ambiguous", 0.3, True  # some keyword overlap but no clear signal of the relationship


def fallback_timeline(prompts: list[PromptRecord]) -> tuple[IntentContract, list[PromptRecord]]:
    """Deterministic multi-prompt fallback. Reuses `fallback_contract`'s sentence-level extraction per
    prompt (no invented requirements) and layers provenance/supersession on top."""
    classified: list[PromptRecord] = []
    all_reqs: list[Requirement] = []
    ambiguities: list[str] = []
    prior_keywords: set[str] = set()
    for p in prompts:
        text = p.text.strip()
        if not text:
            classified.append(p)
            continue
        cls, confidence, ambiguous = classify_prompt_fallback(prior_keywords, text)
        supersedes: list[str] = []
        if cls in {"correction", "replacement"}:
            kw = keywords(text)
            for i, req in enumerate(all_reqs):
                if req.active and overlap(kw, keywords(req.text)):
                    all_reqs[i] = req.model_copy(update={"active": False, "superseded_by": p.id})
                    if req.origin_prompt_id:
                        supersedes.append(req.origin_prompt_id)
        refines = [classified[-1].id] if cls in {"refinement", "continuation"} and classified else []
        record = p.model_copy(update={
            "classification": cls, "confidence": confidence, "ambiguous": ambiguous,
            "supersedes": sorted(set(supersedes)), "refines": refines,
        })
        classified.append(record)
        if ambiguous:
            ambiguities.append(f"P{p.seq} ({record.id}): relationship to earlier prompts is unclear "
                               f"({text[:80]!r}); needs developer review.")
        local = fallback_contract(text)
        for req in local.requirements:
            all_reqs.append(req.model_copy(update={"id": f"R{len(all_reqs) + 1}", "origin_prompt_id": record.id}))
        prior_keywords |= keywords(text)
    all_reqs = all_reqs[:MAX_REQUIREMENTS]
    first_text = next((p.text.strip() for p in prompts if p.text.strip()), "")
    summary = first_text[:80] or "(no developer prompt captured)"
    contract = IntentContract(task_summary=summary, requirements=all_reqs, ambiguities=ambiguities)
    return contract, classified
