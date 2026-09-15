"""Selective reads from the Claude Code transcript (JSONL). Never loaded wholesale into model context."""

import json
import re
from pathlib import Path

CLAIM_PATTERNS = {
    "tests_pass": re.compile(r"\b(all\s+)?(the\s+)?tests?\s+(now\s+)?(pass|passes|passing|succeed|are green)", re.I),
    "build_pass": re.compile(r"\bbuild\s+(now\s+)?(passes|succeeds|is green|works)", re.I),
    "no_schema_change": re.compile(r"(no\s+(database\s+)?(schema|migration)s?\s+change|schema\s+(was|is)\s+(not|un))", re.I),
    "no_dependency_change": re.compile(r"no\s+(new\s+)?dependenc", re.I),
    "behavior_preserved": re.compile(
        r"(unchanged|backwards?[\s-]compatible|preserv\w+|no existing (functionality|behavio\w+)\s+(was\s+)?changed"
        r"|continues? to work)", re.I,
    ),
}


def final_assistant_text(transcript_path: str | None) -> str | None:
    if not transcript_path or not Path(transcript_path).is_file():
        return None
    last = None
    with open(transcript_path, errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message") if isinstance(entry, dict) else None
            if entry.get("type") != "assistant" or not isinstance(message, dict):
                continue
            content = message.get("content")
            texts = [content] if isinstance(content, str) else [
                b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"
            ]
            if text := "\n".join(t for t in texts if t).strip():
                last = text
    return last


def extract_claims(text: str) -> list[tuple[str, str]]:
    """(category, sentence) pairs; regex only, so no model tokens are spent on extraction."""
    claims = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = sentence.strip(" -*•\t")
        for category, pattern in CLAIM_PATTERNS.items():
            if sentence and pattern.search(sentence):
                claims.append((category, sentence[:300]))
                break
    return claims
