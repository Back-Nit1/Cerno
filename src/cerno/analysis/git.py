"""Native git via subprocess (argument arrays, shell=False).

Attribution works on *tree snapshots* of the full working tree: the baseline tree captures the developer's
pre-existing dirty state, so diff(baseline_tree, final_tree) contains only what changed during the session.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from cerno.models import FileChange

MANIFESTS = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pyproject.toml", "uv.lock",
    "poetry.lock", "Pipfile", "Pipfile.lock", "go.mod", "Cargo.toml",
}


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str, env: dict | None = None, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, errors="replace",
        env={**os.environ, **(env or {})}, shell=False,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def get_repository_root(path: Path) -> Path | None:
    try:
        return Path(git(path, "rev-parse", "--show-toplevel").strip())
    except (GitError, FileNotFoundError, NotADirectoryError):
        return None


def get_head(root: Path) -> str | None:
    out = git(root, "rev-parse", "--verify", "-q", "HEAD", check=False).strip()
    return out or None


def get_status(root: Path) -> list[str]:
    return [line for line in git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines() if line]


def get_untracked_files(root: Path) -> list[str]:
    return [line for line in git(root, "ls-files", "--others", "--exclude-standard").splitlines() if line]


def is_manifest(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in MANIFESTS or (name.startswith("requirements") and name.endswith(".txt"))


def is_schema(path: str) -> bool:
    return path.endswith((".prisma", ".sql")) or "/migrations/" in f"/{path}"


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        any(part in {"test", "tests", "__tests__", "spec"} for part in path.split("/")[:-1])
        or ".test." in name or ".spec." in name or name.startswith("test_") or name.endswith("_test.py")
    )


def snapshot_tree(root: Path) -> str:
    """Write a tree object for the current working tree without touching the user's real index."""
    real_index = Path(git(root, "rev-parse", "--git-path", "index").strip())
    real_index = real_index if real_index.is_absolute() else root / real_index
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index"
        if real_index.exists():
            shutil.copy(real_index, index)  # reuse stat cache so unchanged files are not rehashed
        env = {"GIT_INDEX_FILE": str(index)}
        git(root, "add", "-A", "--", ".", ":(exclude).cerno", env=env)
        return git(root, "write-tree", env=env).strip()


def pin_tree(root: Path, ref: str, tree: str) -> None:
    """Keep snapshot trees reachable so `git gc` does not prune them."""
    git(root, "update-ref", f"refs/cerno/{ref}", tree, check=False)


def head_tree(root: Path) -> str:
    if get_head(root):
        return git(root, "rev-parse", "HEAD^{tree}").strip()
    return git(root, "hash-object", "-t", "tree", "/dev/null").strip()  # empty tree


def get_changed_files(root: Path, base: str, final: str) -> list[FileChange]:
    raw = git(root, "diff-tree", "-r", "-M", "--raw", "-z", "--no-commit-id", base, final).split("\0")
    numstat = git(root, "diff-tree", "-r", "-M", "--numstat", "-z", "--no-commit-id", base, final).split("\0")
    stats: dict[str, tuple[int, int]] = {}
    i = 0
    while i < len(numstat) and numstat[i]:
        adds, dels, path = numstat[i].split("\t", 2)
        if not path:  # rename: old\0new follow
            path, i = numstat[i + 2], i + 2
        stats[path] = (int(adds) if adds != "-" else 0, int(dels) if dels != "-" else 0)
        i += 1
    changes, i = [], 0
    names = {"A": "created", "M": "modified", "D": "deleted", "R": "renamed", "T": "modified", "C": "created"}
    while i < len(raw) and raw[i]:
        _, _, old_hash, new_hash, code = raw[i][1:].split(" ")
        if code[0] in "RC":
            old_path, path, i = raw[i + 1], raw[i + 2], i + 3
        else:
            old_path, path, i = None, raw[i + 1], i + 2
        adds, dels = stats.get(path, (0, 0))
        changes.append(
            FileChange(
                path=path, status=names.get(code[0], "modified"), additions=adds, deletions=dels,
                baseline_hash=None if set(old_hash) == {"0"} else old_hash,
                final_hash=None if set(new_hash) == {"0"} else new_hash, old_path=old_path,
            )
        )
    return changes


def get_file_diff(root: Path, base: str, final: str, path: str, context: int = 3) -> str:
    return git(root, "diff", f"-U{context}", "-M", base, final, "--", path)


def get_diff_hunks(diff: str) -> list[dict]:
    """Parse unified diff into hunks: new_start/new_len plus added/removed lines."""
    hunks = []
    for line in diff.splitlines():
        if line.startswith("@@"):
            old, new = line.split("@@")[1].split()
            o_start, _, o_len = old[1:].partition(",")
            n_start, _, n_len = new[1:].partition(",")
            hunks.append({
                "old_start": int(o_start), "old_len": int(o_len or 1),
                "new_start": int(n_start), "new_len": int(n_len or 1), "added": [], "removed": [],
            })
        elif hunks and line.startswith("+") and not line.startswith("+++"):
            hunks[-1]["added"].append(line[1:])
        elif hunks and line.startswith("-") and not line.startswith("---"):
            hunks[-1]["removed"].append(line[1:])
    return hunks


def read_blob(root: Path, tree: str, path: str) -> str | None:
    proc = subprocess.run(["git", "cat-file", "blob", f"{tree}:{path}"], cwd=root, capture_output=True)
    if proc.returncode != 0 or b"\0" in proc.stdout[:8000]:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def list_tree_files(root: Path, tree: str) -> list[str]:
    return [p for p in git(root, "ls-tree", "-r", "--name-only", "-z", tree).split("\0") if p]


def tree_hashes(root: Path, tree: str, predicate) -> dict[str, str]:
    out = {}
    for line in git(root, "ls-tree", "-r", "-z", tree).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            if predicate(path):
                out[path] = meta.split()[2]
    return out


def get_manifest_changes(changes: list[FileChange]) -> list[FileChange]:
    return [c for c in changes if is_manifest(c.path)]


def content_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()
