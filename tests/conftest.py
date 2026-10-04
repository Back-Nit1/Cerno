import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cerno.config import Settings, db_file
from cerno.storage.sqlite import DB


def run_git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                   capture_output=True)


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


@pytest.fixture
def repo(tmp_path: Path):
    """A committed git repo; returns (root, write-helper)."""
    run_git(tmp_path, "init", "-q")
    write(tmp_path, {".gitignore": ".cerno/\n", "README.md": "hi\n"})
    run_git(tmp_path, "add", "-A")
    run_git(tmp_path, "commit", "-qm", "init")
    return tmp_path


BASE_AUTH = """function requireAdmin(user) {
  if (!user || !user.isAdmin) {
    throw new Error("forbidden");
  }
  return true;
}

function deleteProject(user, id) {
  requireAdmin(user);
  return db.remove(id);
}

module.exports = { requireAdmin, deleteProject };
"""

HEAD_AUTH = BASE_AUTH.replace("  requireAdmin(user);\n", "")

TEST_FILE = """const { deleteProject } = require("../src/auth");

test("deleteProject rejects non-admins", () => {
  deleteProject({}, 1);
});
"""

PACKAGE = json.dumps({"name": "demo", "scripts": {"test": "node -e \"process.exit(0)\""}}, indent=2) + "\n"


@pytest.fixture
def review_repo(repo):
    """main holds the guarded version; `feature` (checked out) removed the permission check."""
    write(repo, {"src/auth.js": BASE_AUTH, "tests/auth.test.js": TEST_FILE, "package.json": PACKAGE})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "base")
    run_git(repo, "branch", "-M", "main")
    run_git(repo, "checkout", "-qb", "feature")
    write(repo, {"src/auth.js": HEAD_AUTH})
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-qm", "drop the admin check")
    return repo


def offending_line(repo) -> int:
    lines = (repo / "src/auth.js").read_text().splitlines()
    return lines.index("  return db.remove(id);") + 1


@pytest.fixture(scope="session")
def demo(tmp_path_factory, monkeypatch_session):
    """Demo repo captured through hooks and analyzed without a model key (runs `node --test`)."""
    from cerno.demo import build_demo
    from cerno.pipeline import analyze

    monkeypatch_session.delenv("OPENAI_API_KEY", raising=False)
    root = tmp_path_factory.mktemp("demo") / "app"
    session_id = build_demo(root)
    settings = Settings.load(root)
    analyze(root, session_id, settings)
    return SimpleNamespace(root=root, session_id=session_id, settings=settings, db=DB(db_file(root, settings)))


@pytest.fixture(scope="session")
def monkeypatch_session():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def block(type_: str, **fields):
    return SimpleNamespace(type=type_, **fields)


class FakeClient:
    """Scripted stand-in for OpenAI: returns queued Responses API outputs and records requests."""

    def __init__(self, responses):
        self._queued, self.requests = list(responses), []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        output = self._queued.pop(0) if self._queued else [block("message", content=[])]
        output = output(**kwargs) if callable(output) else output
        usage = SimpleNamespace(input_tokens=100, output_tokens=20,
                                input_tokens_details=SimpleNamespace(cached_tokens=0))
        return SimpleNamespace(output=output, usage=usage)
