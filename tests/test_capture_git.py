import json

from conftest import run_git, write

from cerno.analysis import git
from cerno.capture.hooks import handle_hook, install_hooks, run_hook_safely
from cerno.config import Settings, db_file
from cerno.storage.sqlite import DB


def test_git_detects_create_modify_delete_and_untracked(repo):
    write(repo, {"a.js": "export const a = 1;\n", "b.js": "export const b = 1;\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "files")
    base = git.snapshot_tree(repo)
    write(repo, {"a.js": "export const a = 2;\nexport const c = 3;\n", "new.js": "x\n"})
    (repo / "b.js").unlink()
    assert "new.js" in git.get_untracked_files(repo)
    changes = {c.path: c for c in git.get_changed_files(repo, base, git.snapshot_tree(repo))}
    assert changes["a.js"].status == "modified" and changes["a.js"].additions == 2 and changes["a.js"].deletions == 1
    assert changes["b.js"].status == "deleted" and changes["b.js"].final_hash is None
    assert changes["new.js"].status == "created" and changes["new.js"].baseline_hash is None
    hunks = git.get_diff_hunks(git.get_file_diff(repo, base, git.snapshot_tree(repo), "a.js"))
    assert hunks[0]["removed"] == ["export const a = 1;"] and "export const c = 3;" in hunks[0]["added"]


def test_snapshot_does_not_touch_real_index(repo):
    write(repo, {"x.txt": "untracked\n"})
    git.snapshot_tree(repo)
    assert "?? x.txt" in git.get_status(repo)


def test_baseline_excludes_preexisting_dirty_changes(repo):
    write(repo, {"README.md": "developer wip\n", "notes.txt": "dev notes\n"})  # dirty before Claude starts
    base = {"session_id": "s1", "cwd": str(repo)}
    handle_hook("user-prompt", {**base, "prompt": "Add a greeting helper please"})
    write(repo, {"greet.js": "export function greet() { return 'hi'; }\n"})
    handle_hook("stop", base)
    snaps = [json.loads((repo / ".cerno/sessions/s1" / f"{k}.json").read_text()) for k in ("baseline", "final")]
    changed = [c.path for c in git.get_changed_files(repo, snaps[0]["tree"], snaps[1]["tree"])]
    assert changed == ["greet.js"]
    assert "README.md" in snaps[0]["dirty_files"]


def test_hook_events_persist_and_normalize(repo):
    payload = {"session_id": "s2", "cwd": str(repo), "transcript_path": "/tmp/t.jsonl", "tool_name": "Edit",
               "tool_input": {"file_path": str(repo / "src/a.ts")}, "tool_response": {"success": True}}
    event = handle_hook("post-tool", payload)
    db = DB(db_file(repo, Settings.load(repo)))
    stored = db.list("events", "s2")
    assert stored[0]["event_type"] == "post_tool_use" and stored[0]["path"] == "src/a.ts" and stored[0]["success"]
    assert db.get("sessions", "s2", "s2")["transcript_path"] == "/tmp/t.jsonl"
    raw_file, offset = event.raw_payload_path.split("#")
    line = (repo / raw_file).read_text()[int(offset):].splitlines()[0]
    assert json.loads(line)["payload"]["tool_name"] == "Edit"


def test_invalid_hook_payload_never_raises(repo, monkeypatch):
    monkeypatch.chdir(repo)
    run_hook_safely("post-tool", "{not json")
    run_hook_safely("stop", "[1, 2]")
    run_hook_safely("user-prompt", "")
    assert "JSONDecodeError" in (repo / ".cerno/hook-errors.log").read_text()


def test_install_hooks_preserves_existing_settings(repo):
    settings = repo / ".claude/settings.json"
    existing = {"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    write(repo, {".claude/settings.json": json.dumps(existing)})
    result = install_hooks(repo)
    data = json.loads(settings.read_text())
    assert data["model"] == "x"
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo mine"
    assert any("cerno hook stop" in h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"])
    assert data["hooks"]["PostToolUse"][0]["matcher"] == "Edit|Write|MultiEdit|NotebookEdit|Bash"
    assert result["backup"] and json.loads(open(result["backup"]).read()) == existing
    assert install_hooks(repo)["added"] == []  # idempotent
    assert ".cerno/" in (repo / ".gitignore").read_text()
