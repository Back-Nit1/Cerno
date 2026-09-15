"""Realistic end-to-end example (see docs/history.md): two development sessions, a known-good
release, a later production failure, and a diagnosis that ranks the regressing commit and links
it back to the session and prompt that introduced it."""

from conftest import run_git, write

from cerno.analysis import history
from cerno.capture.hooks import handle_hook
from cerno.config import Settings, db_file
from cerno.models import Session
from cerno.storage.sqlite import DB
from cerno.verification.diagnosis import diagnose


def test_regression_traced_back_to_the_session_that_introduced_it(repo):
    db = DB(db_file(repo, Settings.load(repo)))

    # Session 1: Claude adds a real login check.
    base1 = {"session_id": "session-1-login", "cwd": str(repo)}
    handle_hook("user-prompt", {**base1, "prompt": "Add a login endpoint that checks the password"})
    write(repo, {"auth.py": "def login(password):\n    return password == get_stored_password()\n",
                "store.py": "def get_stored_password():\n    return 'hunter2'\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "Add login endpoint")
    handle_hook("stop", base1)

    history.index(repo, db)
    repo_id = history.repo_id_for(repo)
    good_release = history.log(db, repo_id, limit=1)[0].hash
    history.add_marker(db, repo_id, "good", good_release, "v1.0: login shipped and verified")

    # Session 2, later: Claude "simplifies" the check and silently breaks it.
    base2 = {"session_id": "session-2-refactor", "cwd": str(repo)}
    handle_hook("user-prompt", {**base2, "prompt": "Refactor the login check to be a one-liner"})
    write(repo, {"auth.py": "def login(password):\n    return True  # TODO: restore real check\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "Simplify login check")
    handle_hook("stop", base2)

    identity = history.index(repo, db)
    bad_release = identity.latest_indexed_commit

    # Production incident weeks later: "login accepts any password". Diagnose from the last known
    # good release (no --bad given: it falls back to current HEAD).
    settings = Settings.load(repo)
    result = diagnose(repo, db, settings, suspected_path="auth.py", query="login accepts any password")

    assert result.candidate_commits == [bad_release]  # exactly one commit sits between good and HEAD
    top = result.suspects[0]
    assert top.ref == bad_release and top.evidence == "fact"

    # Link the suspect commit back to the session, prompt, and confirm it was really committed then.
    session2 = Session.model_validate(db.get("sessions", "session-2-refactor", "session-2-refactor"))
    link = history.link_session(repo, db, session2)
    assert bad_release in link.commits_created

    prov = history.file_provenance(repo, db, "session-2-refactor", "auth.py")
    assert prov["prompt"]["text"].startswith("Refactor the login check")
    assert prov["committed_during_session"] is True
