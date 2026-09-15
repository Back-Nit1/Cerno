"""What may leave the machine: path exclusion and secret redaction."""

import fnmatch
import re
from functools import lru_cache
from pathlib import Path

ALWAYS_EXCLUDED = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", ".npmrc", ".pypirc",
    ".netrc", "credentials*", "*.keystore", ".cerno/*", ".git/*",
]

SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "assigned_secret",
        re.compile(
            r"""(?i)\b(pass(word)?|secret|api[_-]?key|token|client[_-]?secret)\b\s*[:=]\s*["'][^"'\s]{8,}["']"""
        ),
    ),
]


@lru_cache(maxsize=16)
def _cernoignore(root: str) -> tuple[str, ...]:
    path = Path(root) / ".cernoignore"
    if not path.is_file():
        return ()
    lines = (line.strip() for line in path.read_text().splitlines())
    return tuple(line for line in lines if line and not line.startswith("#"))


def is_excluded(root: Path, path: str) -> bool:
    """`.gitignore` is honored upstream (snapshots come from `git add -A`); this adds credentials + .cernoignore."""
    name = path.rsplit("/", 1)[-1]
    for pattern in (*ALWAYS_EXCLUDED, *_cernoignore(str(root))):
        pattern = pattern.rstrip("/")
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern) or path.startswith(pattern + "/"):
            return True
    return False


def find_secrets(text: str) -> list[tuple[str, str]]:
    """Return (kind, matched line) for suspicious strings. Suspicious, not proven secrets."""
    hits = []
    for line in text.splitlines():
        for kind, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                hits.append((kind, line.strip()[:200]))
                break
    return hits


def redact(text: str) -> str:
    for kind, pattern in SECRET_PATTERNS:
        text = pattern.sub(f"[REDACTED:{kind}]", text)
    return text
