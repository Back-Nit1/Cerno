"""Claude Code hook ingestion and safe installation. OBSERVE mode: never block, never crash Claude Code."""

import json
import shutil
import subprocess
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from cerno.analysis import git
from cerno.capture.snapshots import now, take_snapshot
from cerno.config import Settings, cerno_dir, db_file, session_dir
from cerno.models import HookEvent, PromptRecord, Session
from cerno.storage.sqlite import DB

HOOK_EVENTS = {
    "session-start": "SessionStart",
    "user-prompt": "UserPromptSubmit",
    "pre-tool": "PreToolUse",
    "post-tool": "PostToolUse",
    "post-tool-failure": "PostToolUseFailure",
    "stop": "Stop",
    "stop-failure": "StopFailure",
}
TOOL_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash"
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}


def _snake(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name).lstrip("_")


def _meaningful(prompt: str) -> bool:
    return len(prompt.strip()) >= 12 and not prompt.lstrip().startswith("/")


def handle_hook(hook: str, payload: dict, cwd: Path | None = None) -> HookEvent | None:
    event_name = HOOK_EVENTS[hook]
    start = Path(payload.get("cwd") or cwd or Path.cwd())
    root = git.get_repository_root(start) or start
    settings = Settings.load(root)
    db = DB(db_file(root, settings))
    session_id = str(payload.get("session_id") or "unknown")
    sdir = session_dir(root, session_id)

    raw_path = sdir / "events.jsonl"
    with open(raw_path, "a") as fh:
        offset = fh.tell()
        fh.write(json.dumps({"hook": event_name, "received_at": now(), "payload": payload}) + "\n")

    stored = db.get("sessions", session_id, session_id)
    session = Session.model_validate(stored) if stored else Session(
        id=session_id, repo_root=str(root), created_at=now(), updated_at=now()
    )
    session.transcript_path = payload.get("transcript_path") or session.transcript_path
    session.updated_at = now()

    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    response = payload.get("tool_response")
    success = None
    if event_name == "PostToolUse":
        success = not (isinstance(response, dict) and (response.get("is_error") or response.get("error")))
    elif event_name in {"PostToolUseFailure", "StopFailure"}:
        success = False
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    if path and Path(path).is_absolute():
        try:
            path = str(Path(path).resolve().relative_to(root.resolve()))
        except ValueError:
            pass

    event = HookEvent(
        event_id=str(uuid.uuid4()), session_id=session_id, event_type=_snake(event_name), timestamp=now(),
        tool_name=payload.get("tool_name"), path=path, command=tool_input.get("command"), success=success,
        raw_payload_path=f"{raw_path.relative_to(root)}#{offset}",
    )
    is_git = git.get_repository_root(start) is not None

    if event_name == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "")
        if _meaningful(prompt):
            session.prompts.append(prompt)
            if is_git:
                # The initial baseline is captured once, at the same moment as the first prompt's
                # checkpoint, so the "baseline -> prompt 1" segment is empty by construction.
                if not (sdir / "baseline.json").exists():
                    take_snapshot(root, session_id, "baseline")
                seq = db.count("prompt_timeline", session_id) + 1
                prompt_record_id = f"{session_id}-P{seq}"
                checkpoint = take_snapshot(root, session_id, "prompt", label=f"prompt-{seq}",
                                           prompt_id=prompt_record_id)
                record = PromptRecord(
                    id=prompt_record_id, session_id=session_id, seq=seq, timestamp=now(), text=prompt,
                    head=checkpoint.head, tree=checkpoint.tree,
                )
                db.put("prompt_timeline", session_id, record.id, record, seq=seq)
    elif event_name in {"Stop", "StopFailure"}:
        if is_git:
            take_snapshot(root, session_id, "final")
        session.status = "stopped" if event_name == "Stop" else "stop_failed"
    elif event_name == "SessionStart":
        session.status = "active"

    db.put("sessions", session_id, session_id, session)
    db.put("events", session_id, event.event_id, event, seq=db.count("events", session_id))
    return event


def run_hook_safely(hook: str, stdin_text: str) -> None:
    """Entry for `cerno hook <name>`. Any failure is logged and swallowed (exit 0)."""
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
        handle_hook(hook, payload)
    except Exception:  # noqa: BLE001 - fail open by design
        try:
            log = cerno_dir(Path.cwd()) / "hook-errors.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a") as fh:
                fh.write(f"--- {now()} {hook}\n{traceback.format_exc()}\n")
        except OSError:
            pass


def hook_command(hook: str) -> str:
    return f'"{sys.executable}" -m cerno hook {hook}'


def install_hooks(root: Path) -> dict:
    """Merge Cerno hooks into .claude/settings.json without removing anything. Returns a summary."""
    settings_path = root / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    data: dict = {}
    if settings_path.exists():
        data = json.loads(settings_path.read_text() or "{}")
        backup = settings_path.with_name(f"settings.json.bak-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy2(settings_path, backup)
    hooks = data.setdefault("hooks", {})
    added = []
    for hook, event_name in HOOK_EVENTS.items():
        groups = hooks.setdefault(event_name, [])
        present = any(
            f"cerno hook {hook}" in h.get("command", "") for g in groups for h in g.get("hooks", [])
        )
        if present:
            continue
        group: dict = {"hooks": [{"type": "command", "command": hook_command(hook), "timeout": 60}]}
        if event_name in TOOL_EVENTS:
            group = {"matcher": TOOL_MATCHER, **group}
        groups.append(group)
        added.append(event_name)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")

    gitignore = root / ".gitignore"
    gitignore_updated = False
    if git.get_repository_root(root) and not _is_ignored(root):
        existing = gitignore.read_text() if gitignore.exists() else ""
        gitignore.write_text(existing + ("" if existing.endswith("\n") or not existing else "\n") + ".cerno/\n")
        gitignore_updated = True
    cerno_dir(root).mkdir(exist_ok=True)
    return {"settings": str(settings_path), "backup": str(backup) if backup else None, "added": added,
            "gitignore_updated": gitignore_updated}


def _is_ignored(root: Path) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", ".cerno/"], cwd=root, capture_output=True).returncode == 0
