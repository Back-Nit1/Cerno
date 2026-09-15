from conftest import run_git, write
from fastapi.testclient import TestClient

from cerno.analysis import history
from cerno.api.app import create_app
from cerno.config import Settings, db_file
from cerno.storage.sqlite import DB


def _client(repo, commit_count: int = 5) -> TestClient:
    for i in range(commit_count):
        write(repo, {f"f{i}.py": "1\n"})
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-qm", f"commit {i}")
    db = DB(db_file(repo, Settings.load(repo)))
    history.index(repo, db)
    return TestClient(create_app(repo))


def test_history_commits_endpoint_paginates_without_sending_everything_at_once(repo):
    client = _client(repo)
    first = client.get("/api/history/commits?limit=2").json()
    assert len(first["commits"]) == 2
    assert first["next_before_seq"] is not None

    second = client.get(f"/api/history/commits?limit=2&before_seq={first['next_before_seq']}").json()
    assert len(second["commits"]) == 2
    assert {c["hash"] for c in first["commits"]}.isdisjoint({c["hash"] for c in second["commits"]})


def test_history_status_commit_detail_and_path_endpoints(repo):
    client = _client(repo, commit_count=2)
    status = client.get("/api/history/status").json()
    assert status["repo_id"] and status["latest_indexed_commit"]

    top_hash = client.get("/api/history/commits?limit=1").json()["commits"][0]["hash"]
    detail = client.get(f"/api/history/commits/{top_hash}").json()
    assert detail["hash"] == top_hash

    assert client.get("/api/history/commits/doesnotexist").status_code == 404

    path = client.get("/api/history/path?path=f0.py").json()
    assert path["commits"] and path["created"]["change_type"] == "created"


def test_history_markers_endpoints_round_trip(repo):
    client = _client(repo, commit_count=1)
    added = client.post("/api/history/markers", json={"kind": "good", "description": "release 1.0"}).json()
    assert added["kind"] == "good"
    listed = client.get("/api/history/markers").json()
    assert listed[0]["id"] == added["id"]
    assert client.get("/api/history/markers?kind=bad").json() == []


def test_history_diagnose_endpoint_returns_a_result(repo, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = _client(repo, commit_count=2)
    result = client.post("/api/history/diagnose", json={"query": "commit"}).json()
    assert result["model_used"] is False
    assert "id" in result
    fetched = client.get(f"/api/history/diagnose/{result['id']}").json()
    assert fetched["id"] == result["id"]
