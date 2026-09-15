from conftest import write

from cerno.analysis import git
from cerno.analysis.graph import RepoIndex, bfs
from cerno.analysis.retrieval import rank
from cerno.analysis.symbols import changed_symbols
from cerno.analysis.tree_sitter import parse_source
from cerno.models import IntentContract, Requirement

TS = """import { createSession } from "./session";
export class AuthService {
  login(user: string) { return createSession(user); }
}
export const helper = (x: number) => x * 2;
export function authenticate(u: string) {
  return new AuthService().login(u);
}
"""


def test_tree_sitter_extracts_symbols_imports_ranges():
    syntax = parse_source("src/auth.ts", TS)
    by_name = {s.name: s for s in syntax.symbols}
    assert {"AuthService", "AuthService.login", "helper", "authenticate"} <= by_name.keys()
    assert by_name["AuthService"].kind == "class" and by_name["AuthService.login"].kind == "method"
    assert (by_name["AuthService"].start_line, by_name["AuthService"].end_line) == (2, 4)
    assert by_name["AuthService.login"].calls == ["createSession"]
    assert syntax.imports == ["./session"]
    assert {"AuthService", "helper", "authenticate"} <= set(syntax.exports)
    py = parse_source("m.py", "from .db import save\nclass A:\n    def run(self):\n        save()\n")
    assert [s.name for s in py.symbols] == ["A", "A.run"] and py.imports == [".db"]


def test_changed_symbols_added_modified_deleted():
    base = RepoIndex({"src/s.ts": "export function a() { return 1; }\nexport function gone() {}\n",
                      "src/c.ts": "import { a } from './s';\nexport function caller() { return a(); }\n"})
    final = RepoIndex({"src/s.ts": "export function a() { return 2; }\nexport function added() {}\n",
                       "src/c.ts": "import { a } from './s';\nexport function caller() { return a(); }\n"})
    from cerno.models import FileChange

    change = FileChange(path="src/s.ts", status="modified", additions=2, deletions=2, baseline_hash="x", final_hash="y")
    result = {s.symbol_name: s for s in changed_symbols([change], base, final)}
    assert result["a"].change_type == "modified" and result["a"].callers == ["src/c.ts::caller"]
    assert result["added"].change_type == "added" and result["gone"].change_type == "deleted"
    assert final.imports["src/c.ts"] == {"src/s.ts"}


def test_bfs_respects_depth_and_cycles():
    graph = {"a": {"b"}, "b": {"c", "a"}, "c": {"d", "a"}, "d": {"e"}}
    assert bfs(graph, "a", 2) == {"a": 0, "b": 1, "c": 2}
    assert bfs(graph, "a", 10) == {"a": 0, "b": 1, "c": 2, "d": 3, "e": 4}


def _ctx(repo, files_before, files_after, cernoignore=""):
    from cerno.config import Settings, db_file
    from cerno.evidence.store import EvidenceStore
    from cerno.storage.sqlite import DB
    from cerno.verification.context import Context

    write(repo, {**files_before, ".cernoignore": cernoignore})
    base = git.snapshot_tree(repo)
    write(repo, files_after)
    final = git.snapshot_tree(repo)
    settings = Settings()
    db = DB(db_file(repo, settings))
    bi, fi = RepoIndex.from_tree(repo, base, db), RepoIndex.from_tree(repo, final, db)
    changes = git.get_changed_files(repo, base, final)
    req = Requirement(id="R1", text="Session expiry must not change", type="invariant", priority="critical",
                      explicit=True)
    contract = IntentContract(task_summary="t", requirements=[req])
    ctx = Context(repo, "s", settings, db, EvidenceStore(db, "s"), base, final, changes,
                  changed_symbols(changes, bi, fi), bi, fi, [], contract)
    return ctx, req


def test_retrieval_prioritizes_changed_symbols_limits_and_ignores(repo):
    before = {f"src/util{i}.ts": f"export function util{i}() {{ return {i}; }}\n" for i in range(30)}
    before |= {"src/session.ts": "export function sessionExpiry() { return 24; }\n",
               "src/login.ts": "import { sessionExpiry } from './session';\nexport function login() { return sessionExpiry(); }\n",
               "private/keys.ts": "export function sessionSecret() { return 1; }\n"}
    after = {f"src/util{i}.ts": f"export function util{i}() {{ return {i + 1}; }}\n" for i in range(30)}
    after |= {"src/session.ts": "export function sessionExpiry() { return 1; }\n",
              "private/keys.ts": "export function sessionSecret() { return 2; }\n"}
    ctx, req = _ctx(repo, before, after, cernoignore="private/\n")
    ranked = rank(req, ctx)
    assert ranked[0].symbol_id == "src/session.ts::sessionExpiry"
    assert any(c.symbol_id == "src/login.ts::login" for c in ranked)  # caller via impact graph
    assert len(ranked) <= 12
    assert not any(c.path.startswith("private/") for c in ranked)
