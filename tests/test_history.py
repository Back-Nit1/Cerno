import sqlite3
import subprocess

from conftest import run_git, write

from cerno.analysis import history
from cerno.capture.hooks import handle_hook
from cerno.config import Settings, db_file
from cerno.models import Session
from cerno.storage.sqlite import DB


def _db(repo) -> DB:
    return DB(db_file(repo, Settings.load(repo)))


def test_linear_history_and_root_commit(repo):
    write(repo, {"a.py": "x = 1\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "add a.py")
    db = _db(repo)
    identity = history.index(repo, db)
    commits = history.log(db, identity.repo_id, limit=10)
    assert len(commits) == 2  # the `repo` fixture's "init" commit + this one
    root = commits[-1]
    assert root.is_root and root.parents == []
    assert identity.initial_commit == root.hash
    assert identity.latest_indexed_commit == commits[0].hash


def test_merge_commit_tracks_all_parents_first_parent_view_skips_side_branch(repo):
    trunk = _default_branch(repo)
    run_git(repo, "checkout", "-qb", "feature")
    write(repo, {"feature.txt": "f\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "feature work")
    run_git(repo, "checkout", "-q", trunk)
    write(repo, {"main.txt": "m\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "main work")
    run_git(repo, "merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    db = _db(repo)
    identity = history.index(repo, db)
    merge = history.get_commit(db, identity.repo_id, identity.latest_indexed_commit)
    assert merge.is_merge and len(merge.parents) == 2
    fp_subjects = [c.subject for c in history.first_parent_log(db, identity.repo_id)]
    assert merge.hash == history.first_parent_log(db, identity.repo_id)[0].hash
    assert "feature work" not in fp_subjects  # first-parent view documents skipping the side branch
    all_subjects = {c.subject for c in history.log(db, identity.repo_id, limit=10)}
    assert "feature work" in all_subjects  # but it is still indexed and reachable


def _default_branch(repo) -> str:
    return subprocess.run(["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True).stdout.strip()


def test_rename_and_delete_tracked_through_path_history(repo):
    write(repo, {"old.py": "1\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "add old.py")
    run_git(repo, "mv", "old.py", "new.py")
    run_git(repo, "commit", "-qm", "rename old to new")
    (repo / "new.py").unlink()
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "delete new.py")
    db = _db(repo)
    identity = history.index(repo, db)
    hits = history.path_history(db, identity.repo_id, "new.py")
    assert len(hits) == 3  # add old.py, rename to new.py, delete new.py -- follows the rename chain back
    edges = history.creation_and_deletion(hits, "new.py")
    assert edges["deleted"] is not None
    rename_commit = next(c for c in hits if any(f.old_path == "old.py" for f in c.files))
    assert next(f for f in rename_commit.files if f.old_path == "old.py").change_type == "renamed"


def test_binary_file_is_flagged(repo):
    (repo / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "add binary")
    db = _db(repo)
    identity = history.index(repo, db)
    commit = history.get_commit(db, identity.repo_id, identity.latest_indexed_commit)
    assert commit.files[0].is_binary


def test_schema_and_manifest_changes_are_flagged(repo):
    write(repo, {"package.json": "{}\n", "migrations/001_init.sql": "CREATE TABLE t (id INT);\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "add manifest and schema")
    db = _db(repo)
    identity = history.index(repo, db)
    commit = history.get_commit(db, identity.repo_id, identity.latest_indexed_commit)
    assert commit.manifest_changed and commit.schema_changed


def test_incremental_reindex_is_additive(repo):
    db = _db(repo)
    id1 = history.index(repo, db)
    write(repo, {"more.py": "1\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "more")
    id2 = history.index(repo, db)
    assert id2.latest_indexed_commit != id1.latest_indexed_commit
    assert len(history.log(db, id2.repo_id, limit=10)) == 2


def test_interrupted_indexing_leaves_no_gap_on_resume(repo):
    db = _db(repo)
    history.index(repo, db)  # indexes the fixture's "init" commit
    for i in range(3):
        write(repo, {f"f{i}.py": "1\n"})
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-qm", f"commit {i}")
    partial = history.index(repo, db, limit=2)  # simulates an index run interrupted after 2 of 3 new commits
    assert len(history.log(db, partial.repo_id, limit=10)) == 3  # "init" + 2 of 3 new commits
    resumed = history.index(repo, db)  # must resume exactly, no gap and no duplicate
    all_commits = history.log(db, resumed.repo_id, limit=10)
    assert len(all_commits) == 4
    assert len({c.hash for c in all_commits}) == 4


def test_shallow_clone_indexes_only_available_history(repo, tmp_path):
    for i in range(3):
        write(repo, {f"s{i}.py": "1\n"})
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-qm", f"s{i}")
    shallow = tmp_path / "shallow"
    # `--depth` is silently ignored for local-path clones ("use file:// instead") -- a real URL is required
    # to get an actual shallow clone rather than a full one.
    subprocess.run(["git", "clone", "--depth", "1", f"file://{repo}", str(shallow)], check=True, capture_output=True)
    db = _db(shallow)
    identity = history.index(shallow, db)
    commits = history.log(db, identity.repo_id, limit=10)
    assert len(commits) == 1  # only the tip commit is available in a depth-1 clone
    assert identity.initial_commit == identity.latest_indexed_commit


def test_rewritten_history_does_not_crash_and_indexes_new_tip(repo):
    write(repo, {"a.py": "1\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "a")
    db = _db(repo)
    history.index(repo, db)
    run_git(repo, "reset", "--hard", "HEAD~1")  # simulate a rewritten/diverged branch
    write(repo, {"b.py": "1\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "b (rewritten)")
    id2 = history.index(repo, db)
    commits = {c.hash: c for c in history.log(db, id2.repo_id, limit=20)}
    assert id2.latest_indexed_commit in commits
    assert commits[id2.latest_indexed_commit].subject == "b (rewritten)"


def test_session_commit_link_and_prompt_to_commit_provenance(repo):
    base = {"session_id": "s1", "cwd": str(repo)}
    handle_hook("user-prompt", {**base, "prompt": "Add a greet helper please, and test the linkage"})
    write(repo, {"greet.py": "def greet(): return 'hi'\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "add greet.py")  # committed during the session, but not via a Cerno hook
    handle_hook("stop", base)

    db = _db(repo)
    session = Session.model_validate(db.get("sessions", "s1", "s1"))
    link = history.link_session(repo, db, session)
    assert link.commits_created  # the commit falls between baseline HEAD and final HEAD
    assert link.confidence == "session_attributed"  # no PostToolUse `git commit` event was recorded

    history.index(repo, db)
    prov = history.file_provenance(repo, db, "s1", "greet.py")
    assert prov["found"]
    assert prov["prompt"] is not None  # which prompt preceded the change
    assert prov["committed_during_session"] is True  # was it committed during that session


def test_markers_are_developer_provided_facts_not_inferred(repo):
    write(repo, {"a.py": "1\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "a")
    db = _db(repo)
    identity = history.index(repo, db)
    assert history.list_markers(db, identity.repo_id) == []  # no marker exists just because commits do
    m = history.add_marker(db, identity.repo_id, "good", identity.latest_indexed_commit, "known-good release")
    assert history.list_markers(db, identity.repo_id, kind="good") == [m]
    assert history.list_markers(db, identity.repo_id, kind="bad") == []


def test_migration_preserves_existing_rows_and_adds_history_tables(tmp_path):
    """A pre-history-layer database (only the original tables, user_version 1) must open and gain
    the new tables without losing anything already stored."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    original_tables = ["sessions", "requirements", "events", "file_changes", "symbol_changes", "evidence",
                       "project_checks", "verdicts", "agent_claims", "model_usage", "change_assessments"]
    for t in original_tables:
        conn.execute(f"CREATE TABLE {t} (pk TEXT PRIMARY KEY, session_id TEXT, item_id TEXT, seq INTEGER, data TEXT NOT NULL)")
    conn.execute("INSERT INTO sessions (pk, session_id, item_id, seq, data) VALUES ('s1:s1','s1','s1',0,'{\"id\": \"s1\"}')")
    conn.execute("PRAGMA user_version=1")
    conn.commit()
    conn.close()

    db = DB(path)
    assert db.get("sessions", "s1", "s1") == {"id": "s1"}  # old data survives
    assert db.list("commits", "any-repo-id") == []  # new tables exist and are queryable, just empty
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 2
