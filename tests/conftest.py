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


@pytest.fixture(scope="session")
def demo(tmp_path_factory, monkeypatch_session):
    """Demo repo captured through hooks and analyzed without a model key (runs `node --test`)."""
    from cerno.demo import build_demo
    from cerno.pipeline import analyze

    monkeypatch_session.delenv("ANTHROPIC_API_KEY", raising=False)
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
    """Scripted stand-in for anthropic.Anthropic: returns queued responses and records requests."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        content = self.responses.pop(0) if self.responses else [block("text", text="...")]
        content = content(**kwargs) if callable(content) else content
        usage = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(content=content, usage=usage)
