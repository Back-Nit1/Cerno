"""Incremental repository-history index: commits, markers, and repo identity.

Kept deliberately separate from the per-session capture pipeline (capture/*, analysis/segments.py):
that layer answers "what changed during this one session"; this layer answers "what happened across
the whole repository over time" so a regression from three weeks ago can be traced back to a session.

Default chronological view is first-parent (`first_parent_log`): it follows parents[0] from the tip,
which is the conventional "what actually shipped on this branch" story. All parents of every commit
are still stored, so a full merge graph is always recoverable; nothing is discarded.
"""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from cerno.analysis import git
from cerno.models import CommitFile, CommitRecord, Marker, RepoIdentity, Session, SessionCommitLink
from cerno.storage.sqlite import DB

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
LOG_FIELDS = ["%H", "%P", "%an", "%ae", "%aI", "%cI", "%s"]
LOG_FORMAT = "\x1f".join(LOG_FIELDS)
DEFAULT_LIMIT = 2000  # ponytail: bounded so `history index` never reparses an unbounded repo unasked


def now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_remote(url: str) -> str:
    """Strip credentials/protocol/.git so ssh and https forms of the same remote match."""
    url = re.sub(r"^[a-z]+://(?:[^@/]+@)?", "", url.strip())
    url = re.sub(r"^git@", "", url).replace(":", "/", 1) if url.startswith("git@") else url
    return url.removesuffix(".git").rstrip("/")


def repo_id_for(root: Path) -> str:
    """Remote-derived id when one exists (stable across clones/paths); else a persisted UUID."""
    remote = git.git(root, "remote", "get-url", "origin", check=False).strip()
    if remote:
        import hashlib

        return "r-" + hashlib.sha1(normalize_remote(remote).encode()).hexdigest()[:16]
    id_file = root / ".cerno" / "repo_id"
    if id_file.is_file():
        return id_file.read_text().strip()
    id_file.parent.mkdir(parents=True, exist_ok=True)
    generated = "u-" + uuid.uuid4().hex[:16]
    id_file.write_text(generated + "\n")
    return generated


def default_branch(root: Path) -> str | None:
    head = git.git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    if head:
        return head.rsplit("/", 1)[-1]
    return git.git(root, "branch", "--show-current", check=False).strip() or None


def _refs_by_commit(root: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    raw = git.git(root, "for-each-ref", "--format=%(objectname) %(refname:short)",
                  "refs/heads", "refs/remotes", "refs/tags", check=False)
    for line in raw.splitlines():
        if " " in line:
            commit, ref = line.split(" ", 1)
            out.setdefault(commit, []).append(ref)
    return out


def _binary_paths(root: Path, base: str, final: str) -> set[str]:
    out = set()
    for line in git.git(root, "diff", "--numstat", base, final, check=False).splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0] == "-" and parts[1] == "-":
            out.add(parts[2].split(" => ")[-1].strip("{}"))
    return out


def _commit_files(root: Path, commit: str, parent: str | None) -> list[CommitFile]:
    base = parent or EMPTY_TREE
    changes = git.get_changed_files(root, base, commit)
    binary = _binary_paths(root, base, commit)
    return [
        CommitFile(path=c.path, old_path=c.old_path, change_type=c.status,
                  additions=c.additions, deletions=c.deletions, is_binary=c.path in binary)
        for c in changes
    ]


def _parse_log(root: Path, rev_range: str) -> list[dict]:
    raw = git.git(root, "log", "--topo-order", "--reverse", f"--format={LOG_FORMAT}", rev_range, check=False)
    rows = []
    for line in raw.splitlines():
        if not line:
            continue
        h, parents, an, ae, ad, cd, subject = line.split("\x1f", 6)
        rows.append({"hash": h, "parents": parents.split() if parents else [],
                    "author_name": an, "author_email": ae, "author_date": ad, "committer_date": cd,
                    "subject": subject})
    return rows


def index(root: Path, db: DB, limit: int | None = None,
          on_progress=None) -> RepoIdentity:
    """Index reachable-from-HEAD history not yet indexed, oldest-first, up to `limit` new commits.

    Restartable: `latest_indexed_commit` only advances as each commit is durably written, so a run
    interrupted partway leaves no gap -- the next call resumes exactly where the last one stopped.
    """
    repo_id = repo_id_for(root)
    head = git.get_head(root)
    existing = db.get("repo_identity", repo_id, "identity")
    identity = RepoIdentity.model_validate(existing) if existing else RepoIdentity(
        repo_id=repo_id, path=str(root), history_schema_version=1,
    )
    identity.path = str(root)
    remote = git.git(root, "remote", "get-url", "origin", check=False).strip() or None
    identity.remote_url, identity.remote_normalized = remote, normalize_remote(remote) if remote else None
    identity.default_branch = default_branch(root)

    if head is None:
        db.put("repo_identity", repo_id, "identity", identity)
        return identity

    rev_range = f"{identity.latest_indexed_commit}..{head}" if identity.latest_indexed_commit else head
    rows = _parse_log(root, rev_range)
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        db.put("repo_identity", repo_id, "identity", identity)
        return identity

    refs = _refs_by_commit(root)
    start_seq = db.count("commits", repo_id)
    for i, row in enumerate(rows):
        parent = row["parents"][0] if row["parents"] else None
        files = _commit_files(root, row["hash"], parent)
        record = CommitRecord(
            hash=row["hash"], parents=row["parents"], author_name=row["author_name"],
            author_email=row["author_email"], author_date=row["author_date"],
            committer_date=row["committer_date"], subject=row["subject"],
            refs=refs.get(row["hash"], []), is_merge=len(row["parents"]) > 1,
            is_root=not row["parents"], files=files,
            manifest_changed=any(git.is_manifest(f.path) for f in files),
            schema_changed=any(git.is_schema(f.path) for f in files),
            test_changed=any(git.is_test(f.path) for f in files),
            seq=start_seq + i,
        )
        db.put("commits", repo_id, record.hash, record, seq=record.seq)
        identity.latest_indexed_commit = record.hash
        identity.initial_commit = identity.initial_commit or (record.hash if record.is_root else identity.initial_commit)
        identity.indexed_at = now()
        db.put("repo_identity", repo_id, "identity", identity)
        if on_progress:
            on_progress(i + 1, len(rows))
    if identity.initial_commit is None:
        all_commits = db.list("commits", repo_id)
        roots = [c["hash"] for c in all_commits if c["is_root"]]
        identity.initial_commit = roots[0] if roots else all_commits[0]["hash"]
        db.put("repo_identity", repo_id, "identity", identity)
    return identity


def file_provenance(root: Path, db: DB, session_id: str, path: str) -> dict:
    """Answers, for one path within one session: which prompt preceded the change, was it committed
    during the session, which requirement motivated it, what verification existed, and whether that
    requirement was later superseded. Built entirely from Agent 1's prompt timeline / diff-segment /
    provenance structures (capture.timeline, analysis.segments, Requirement provenance fields) plus
    this layer's session-commit link -- nothing here is recomputed or duplicated."""
    from cerno.analysis.segments import attribute_changes, build_segments
    from cerno.capture.timeline import load_prompt_timeline
    from cerno.models import HookEvent, Session

    stored = db.get("sessions", session_id, session_id)
    if stored is None:
        return {"path": path, "found": False}
    session = Session.model_validate(stored)
    prompts = load_prompt_timeline(session_id, session, db)
    segments = build_segments(root, session_id, prompts, db)
    events = [HookEvent.model_validate(e) for e in db.list("events", session_id)]
    attribution = next((a for a in attribute_changes(segments, events) if a.path == path), None)
    prompt = next((p for p in prompts if attribution and p.id == attribution.prompt_id), None)
    requirements = db.list("requirements", session_id)
    motivating = [r for r in requirements if r.get("origin_prompt_id") == (prompt.id if prompt else None)]
    verdicts = {v["requirement_id"]: v for v in db.list("verdicts", session_id)}
    link = get_session_link(db, repo_id_for(root), session_id)
    return {
        "path": path, "found": True,
        "prompt": prompt.model_dump() if prompt else None,
        "attribution": attribution.model_dump() if attribution else None,
        "committed_during_session": bool(link and link.commits_created),
        "session_commit_link": link.model_dump() if link else None,
        "motivating_requirements": [
            {**r, "verdict": verdicts.get(r["id"]), "superseded": not r.get("active", True)} for r in motivating
        ],
    }


def get_commit(db: DB, repo_id: str, commit_hash: str) -> CommitRecord | None:
    row = db.get("commits", repo_id, commit_hash)
    return CommitRecord.model_validate(row) if row else None


def log(db: DB, repo_id: str, limit: int = 50, before_seq: int | None = None) -> list[CommitRecord]:
    """Newest-first page over the full indexed set (not just first-parent). Paginated for the API/UI."""
    rows = sorted(db.list("commits", repo_id), key=lambda r: r["seq"], reverse=True)
    if before_seq is not None:
        rows = [r for r in rows if r["seq"] < before_seq]
    return [CommitRecord.model_validate(r) for r in rows[:limit]]


def first_parent_log(db: DB, repo_id: str, limit: int = 200) -> list[CommitRecord]:
    """Default chronological product-history view: walk parents[0] from the latest indexed commit."""
    identity = db.get("repo_identity", repo_id, "identity")
    if not identity or not identity.get("latest_indexed_commit"):
        return []
    out, cur = [], identity["latest_indexed_commit"]
    while cur and len(out) < limit:
        record = get_commit(db, repo_id, cur)
        if record is None:
            break
        out.append(record)
        cur = record.parents[0] if record.parents else None
    return out


def path_history(db: DB, repo_id: str, path: str) -> list[CommitRecord]:
    """All indexed commits touching `path`, following renames back through old_path chains."""
    all_commits = sorted((CommitRecord.model_validate(r) for r in db.list("commits", repo_id)),
                        key=lambda c: c.seq, reverse=True)
    paths, out = {path}, []
    for commit in all_commits:
        hit = next((f for f in commit.files if f.path in paths), None)
        if hit:
            out.append(commit)
            if hit.old_path:
                paths.add(hit.old_path)
    return out


def link_session(root: Path, db: DB, session: Session) -> SessionCommitLink:
    """Which commits (if any) were made during this session, and how confident we are of that.

    "session_attributed" means the commits fall strictly between the session's captured baseline
    and final HEAD -- strong temporal bounding, not proof Claude itself ran `git commit` (that would
    require a PostToolUse Bash event recorded for the commit, which is checked first).
    """
    from cerno.capture.snapshots import load_snapshot

    repo_id = repo_id_for(root)
    baseline, final = load_snapshot(root, session.id, "baseline"), load_snapshot(root, session.id, "final")
    b_head, f_head = baseline.head if baseline else None, final.head if final else None
    commits_created: list[str] = []
    if b_head and f_head and b_head != f_head:
        commits_created = [h for h in git.git(root, "rev-list", f"{b_head}..{f_head}", check=False).split() if h]
    events = db.list("events", session.id)
    direct = commits_created and any(
        e.get("event_type") == "post_tool_use" and e.get("command") and "commit" in e["command"] for e in events
    )
    if direct:
        confidence, basis = "direct_tool_event", "a git commit was run as a Bash tool call during this session"
    elif commits_created:
        confidence = "session_attributed"
        basis = f"{len(commits_created)} commit(s) fall between the session's baseline HEAD and final HEAD"
    elif b_head or f_head:
        confidence, basis = "temporal_only", "session captured, but no new commits were made between baseline and final"
    else:
        confidence, basis = "unknown", "no baseline/final HEAD was captured for this session"
    link = SessionCommitLink(
        session_id=session.id, repo_id=repo_id, baseline_head=b_head, final_head=f_head,
        baseline_tree=baseline.tree if baseline else None, final_tree=final.tree if final else None,
        commits_created=commits_created, confidence=confidence, basis=basis,
    )
    db.put("session_commit_links", repo_id, session.id, link)
    return link


def get_session_link(db: DB, repo_id: str, session_id: str) -> SessionCommitLink | None:
    row = db.get("session_commit_links", repo_id, session_id)
    return SessionCommitLink.model_validate(row) if row else None


def add_marker(db: DB, repo_id: str, kind: str, commit: str | None, description: str, metadata: dict | None = None) -> Marker:
    marker = Marker(id=f"M-{uuid.uuid4().hex[:10]}", repo_id=repo_id, kind=kind, commit=commit,
                    timestamp=now(), description=description, metadata=metadata or {})
    db.put("markers", repo_id, marker.id, marker, seq=db.count("markers", repo_id))
    return marker


def list_markers(db: DB, repo_id: str, kind: str | None = None) -> list[Marker]:
    rows = [Marker.model_validate(r) for r in db.list("markers", repo_id)]
    return [m for m in rows if kind is None or m.kind == kind]


def creation_and_deletion(history: list[CommitRecord], path: str) -> dict:
    """`history` must be newest-first (as returned by path_history)."""
    if not history:
        return {"created": None, "deleted": None}
    oldest = history[-1]
    created = next((f for f in oldest.files if f.path == path or f.old_path is None), oldest.files[0] if oldest.files else None)
    deleted_commit = next((c for c in history if any(f.path == path and f.change_type == "deleted" for f in c.files)), None)
    return {
        "created": {"commit": oldest.hash, "change_type": created.change_type if created else "added"},
        "deleted": {"commit": deleted_commit.hash} if deleted_commit else None,
    }
