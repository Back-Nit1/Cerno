"""Baseline / final working-tree snapshots."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from cerno.analysis import git
from cerno.config import session_dir
from cerno.models import Snapshot
from cerno.verification.runtime import detect_checks, detect_project_types


def now() -> str:
    return datetime.now(UTC).isoformat()


def take_snapshot(root: Path, session_id: str, kind: str, label: str | None = None,
                  prompt_id: str | None = None) -> Snapshot:
    """Capture repository state, reusing one git tree object per call. `label` names the file/ref for
    per-prompt checkpoints (e.g. "prompt-3"); it defaults to `kind` for the baseline/final snapshots."""
    label = label or kind
    tree = git.snapshot_tree(root)
    git.pin_tree(root, f"{hashlib.sha1(session_id.encode()).hexdigest()[:16]}/{label}", tree)
    status = git.get_status(root)
    snap = Snapshot(
        kind=kind, repo_root=str(root), head=git.get_head(root), tree=tree, status=status,
        dirty_files=[line[3:] for line in status],
        manifest_hashes=git.tree_hashes(root, tree, git.is_manifest),
        schema_hashes=git.tree_hashes(root, tree, git.is_schema),
        project_types=detect_project_types(root), scripts=detect_checks(root), timestamp=now(),
        prompt_id=prompt_id,
    )
    (session_dir(root, session_id) / f"{label}.json").write_text(snap.model_dump_json(indent=2))
    return snap


def load_snapshot(root: Path, session_id: str, kind: str) -> Snapshot | None:
    path = session_dir(root, session_id) / f"{kind}.json"
    return Snapshot.model_validate_json(path.read_text()) if path.is_file() else None


def load_prompt_snapshot(root: Path, session_id: str, seq: int) -> Snapshot | None:
    return load_snapshot(root, session_id, f"prompt-{seq}")
