import json

from conftest import FakeClient, block, run_git, write

from cerno.analysis import history
from cerno.config import Settings, db_file
from cerno.llm.openai import LLM
from cerno.models import CommitRecord, SuspectFinding
from cerno.storage.sqlite import DB
from cerno.verification.diagnosis import _packet, diagnose


def _index(repo) -> tuple[DB, str]:
    db = DB(db_file(repo, Settings.load(repo)))
    identity = history.index(repo, db)
    return db, identity.repo_id


def test_diagnosis_without_openai_ranks_suspects_deterministically(repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    write(repo, {"auth.py": "def login(): pass\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "add auth.py")
    write(repo, {"auth.py": "def login(): return broken\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "break login")
    write(repo, {"README.md": "unrelated docs update\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "docs")
    db, _ = _index(repo)
    settings = Settings.load(repo)

    result = diagnose(repo, db, settings, suspected_path="auth.py", query="login broken")

    assert result.model_used is False  # works without OpenAI
    assert result.suspects  # facts alone produced ranked suspects
    top = result.suspects[0]
    assert top.evidence == "fact"  # a direct file match is a fact, not a guess
    assert "auth.py" in top.reason
    assert not any("docs" in s.reason for s in result.suspects)  # unrelated commit did not score
    assert result.bisect_plan and "bisect" in result.bisect_plan[0]  # manual plan only, nothing executed


def test_diagnosis_narrows_by_good_bad_range(repo):
    write(repo, {"a.py": "1\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "a")
    db, repo_id = _index(repo)
    good = history.log(db, repo_id, limit=1)[0].hash
    write(repo, {"a.py": "2\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "break a")
    write(repo, {"unrelated.py": "1\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "unrelated")
    history.index(repo, db)
    settings = Settings.load(repo)

    result = diagnose(repo, db, settings, good=good, suspected_path="a.py")

    assert good == result.good
    assert result.bad  # defaulted to current HEAD since only `good` was given
    assert all(c != good for c in result.candidate_commits)  # good itself is excluded from the range


def test_diagnosis_with_fake_openai_narrative(repo):
    write(repo, {"auth.py": "1\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "add auth.py")
    write(repo, {"auth.py": "2\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "tweak auth.py")
    db, repo_id = _index(repo)
    bad = history.log(db, repo_id, limit=1)[0].hash
    settings = Settings.load(repo)

    def scripted(**kwargs):
        args = json.dumps({"narrative": "the auth.py tweak looks causal", "likely_causal_refs": [bad]})
        return [block("function_call", call_id="c1", name="submit_diagnosis", arguments=args)]

    llm = LLM(settings, client=FakeClient([scripted]))
    result = diagnose(repo, db, settings, suspected_path="auth.py", llm=llm)

    assert result.model_used is True  # a fake client is still "available" -- no live network call happened
    assert "causal" in result.narrative
    assert llm.client.requests  # the scripted client recorded exactly the one request made


def test_diagnosis_never_invents_a_suspect_outside_the_candidate_set(repo):
    write(repo, {"auth.py": "1\n"})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "add auth.py")
    db, _ = _index(repo)
    settings = Settings.load(repo)

    def scripted(**kwargs):
        args = json.dumps({"narrative": "ignore me", "likely_causal_refs": ["deadbeef" * 5]})
        return [block("function_call", call_id="c1", name="submit_diagnosis", arguments=args)]

    llm = LLM(settings, client=FakeClient([scripted]))
    result = diagnose(repo, db, settings, suspected_path="auth.py", llm=llm)
    assert all(s.evidence != "model_inference" for s in result.suspects)  # bogus ref was dropped, not trusted


def test_packet_redacts_secrets_before_it_would_be_sent_to_openai():
    commit = CommitRecord(
        hash="abc123", parents=[], author_name="a", author_email="a@a",
        author_date="2026-01-01T00:00:00Z", committer_date="2026-01-01T00:00:00Z",
        subject="oops committed sk-ant-api03-1234567890123456789012345678901234 by accident",
        is_merge=False, is_root=True, files=[],
    )
    finding = SuspectFinding(kind="commit", ref="abc123", score=1.0, reason="test match", evidence="correlation")
    packet = _packet([finding], {"abc123": commit})
    assert "sk-ant-" not in packet
    assert "REDACTED" in packet
