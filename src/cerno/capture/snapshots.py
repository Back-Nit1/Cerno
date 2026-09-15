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


def take_snapshot(root: Path, session_id: str, kind: str) -> Snapshot:
    tree = git.snapshot_tree(root)
    git.pin_tree(root, f"{hashlib.sha1(session_id.encode()).hexdigest()[:16]}/{kind}", tree)
    status = git.get_status(root)
    snap = Snapshot(
        kind=kind, repo_root=str(root), head=git.get_head(root), tree=tree, status=status,
        dirty_files=[line[3:] for line in status],
        manifest_hashes=git.tree_hashes(root, tree, git.is_manifest),
        schema_hashes=git.tree_hashes(root, tree, git.is_schema),
        project_types=detect_project_types(root), scripts=detect_checks(root), timestamp=now(),
    )
    (session_dir(root, session_id) / f"{kind}.json").write_text(snap.model_dump_json(indent=2))
    return snap


def load_snapshot(root: Path, session_id: str, kind: str) -> Snapshot | None:
    path = session_dir(root, session_id) / f"{kind}.json"
    return Snapshot.model_validate_json(path.read_text()) if path.is_file() else None
