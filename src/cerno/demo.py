"""Reproducible demo: a baseline auth app plus a known, subtly flawed Claude change, captured through real hooks."""

import json
import shutil
import subprocess
from pathlib import Path

from cerno.capture.hooks import handle_hook

FIXTURE = Path(__file__).resolve().parents[2] / "examples" / "demo-auth-app"
SESSION_ID = "demo-password-reset"
MARKER = ".cerno-demo"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=Cerno Demo", "-c", "user.email=demo@cerno.local", *args],
                   cwd=root, check=True, capture_output=True)


def build_demo(target: Path, fixture: Path = FIXTURE) -> str:
    target = target.expanduser().resolve()
    if target.exists():
        if not (target / MARKER).exists():
            raise SystemExit(f"{target} exists and is not a Cerno demo directory; refusing to overwrite it")
        shutil.rmtree(target)
    shutil.copytree(fixture / "baseline", target)
    (target / MARKER).write_text("created by `cerno demo`\n")
    (target / ".gitignore").write_text(".cerno/\nnode_modules/\n.cerno-demo\n")
    _git(target, "init", "-q")
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "Baseline auth app")

    # Developer's own uncommitted work before the Claude session: must NOT be attributed to Claude.
    with open(target / "README.md", "a") as fh:
        fh.write("\nLocal notes: investigating login latency (uncommitted developer work).\n")

    transcript = target / ".cerno" / "demo-transcript.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    prompt = (fixture / "prompt.txt").read_text().strip()
    base = {"session_id": SESSION_ID, "cwd": str(target), "transcript_path": str(transcript)}

    handle_hook("session-start", {**base, "hook_event_name": "SessionStart", "source": "startup"})
    handle_hook("user-prompt", {**base, "hook_event_name": "UserPromptSubmit", "prompt": prompt})

    change_root = fixture / "change"
    for src in sorted(p for p in change_root.rglob("*") if p.is_file()):
        rel = src.relative_to(change_root)
        dest = target / rel
        tool = "Edit" if dest.exists() else "Write"
        payload = {**base, "tool_name": tool, "tool_input": {"file_path": str(dest)}}
        handle_hook("pre-tool", {**payload, "hook_event_name": "PreToolUse"})
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
        handle_hook("post-tool", {**payload, "hook_event_name": "PostToolUse", "tool_response": {"success": True}})

    bash = {**base, "tool_name": "Bash", "tool_input": {"command": "npm test"}}
    handle_hook("pre-tool", {**bash, "hook_event_name": "PreToolUse"})
    handle_hook("post-tool", {**bash, "hook_event_name": "PostToolUse", "tool_response": {"stdout": "pass"}})

    final_message = (fixture / "claude_final.md").read_text().strip()
    with open(transcript, "w") as fh:
        fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": final_message}]}}) + "\n")

    handle_hook("stop", {**base, "hook_event_name": "Stop", "stop_hook_active": False})
    return SESSION_ID
