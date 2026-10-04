"""Resolve --base/--head/--staged to the two git trees a review diffs."""

from dataclasses import dataclass
from pathlib import Path

from cerno.analysis import git
from cerno.review.models import Revision

WORKING_TREE = "(working tree)"
STAGED = "(staged)"


@dataclass
class Revisions:
    base: Revision
    head: Revision
    merge_base: str


def _tree_of(root: Path, commit: str) -> str:
    return git.git(root, "rev-parse", "--verify", f"{commit}^{{tree}}").strip()


def _commit(root: Path, ref: str) -> str:
    return git.git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def _branch(root: Path) -> str:
    return git.git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip() or "HEAD"


def staged_tree(root: Path) -> str:
    """Tree of the current index. Read-only: `git write-tree` does not change the index."""
    return git.git(root, "write-tree").strip()


def resolve(root: Path, base: str = "main", head: str | None = None, staged: bool = False) -> Revisions:
    """Raises git.GitError (with git's own message) for an unresolvable ref."""
    if staged:
        head_sha = git.get_head(root)
        base_rev = Revision(ref="HEAD", sha=head_sha, tree=git.head_tree(root))
        return Revisions(base_rev, Revision(ref=STAGED, sha=None, tree=staged_tree(root)), head_sha or "")

    if head is None:
        head_sha, head_ref, head_tree = None, _branch(root), git.snapshot_tree(root)
        head_side = git.get_head(root)
    else:
        head_sha = head_side = _commit(root, head)
        head_ref, head_tree = head, _tree_of(root, head_sha)

    base_sha = _commit(root, base)
    if head_side is None:  # no commits yet: diff everything against the empty tree
        return Revisions(Revision(ref=base, sha=base_sha, tree=_tree_of(root, base_sha)),
                         Revision(ref=head_ref, sha=head_sha, tree=head_tree), base_sha)
    merge_base = git.git(root, "merge-base", base_sha, head_side).strip()
    return Revisions(Revision(ref=base, sha=base_sha, tree=_tree_of(root, merge_base)),
                     Revision(ref=head_ref, sha=head_sha, tree=head_tree), merge_base)
