"""Lightweight impact graph over plain adjacency maps. Relationships are name-based approximations."""

import posixpath
from collections import deque
from pathlib import Path

from cerno.analysis import git
from cerno.analysis.tree_sitter import FileSyntax, parse_source, supported
from cerno.privacy import is_excluded
from cerno.storage.sqlite import DB

MAX_INDEX_FILES = 3000  # ponytail: whole-repo parse capped; switch to on-demand neighborhood parsing for huge repos
MAX_FILE_BYTES = 400_000
RESOLVE_EXTS = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", "/index.ts", "/index.tsx", "/index.js"]


def bfs(adjacency: dict[str, set[str]], start: str, max_depth: int) -> dict[str, int]:
    """Bounded BFS; returns node -> distance. Cycles are never revisited."""
    seen = {start: 0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if seen[node] >= max_depth:
            continue
        for nxt in sorted(adjacency.get(node, ())):
            if nxt not in seen:
                seen[nxt] = seen[node] + 1
                queue.append(nxt)
    return seen


def parse_cached(db: DB | None, path: str, source: str) -> FileSyntax | None:
    if not supported(path):
        return None
    key = f"ast:{path.rsplit('.', 1)[-1]}:{git.content_hash(source)}"
    if db is not None and (hit := db.cache_get(key)) is not None:
        return FileSyntax.model_validate(hit)
    syntax = parse_source(path, source)
    if db is not None and syntax is not None:
        db.cache_put(key, syntax.model_dump())
    return syntax


class RepoIndex:
    """Symbols, calls, and imports for one tree snapshot. symbol_id = "path::QualifiedName"."""

    def __init__(self, sources: dict[str, str], db: DB | None = None):
        self.sources = sources
        self.files: dict[str, FileSyntax] = {}
        for path, source in sources.items():
            if (syntax := parse_cached(db, path, source)) is not None:
                self.files[path] = syntax
        self.symbols = {f"{p}::{s.name}": (p, s) for p, fs in self.files.items() for s in fs.symbols}
        self.by_short_name: dict[str, set[str]] = {}
        for sid, (_, sym) in self.symbols.items():
            self.by_short_name.setdefault(sym.name.rsplit(".", 1)[-1], set()).add(sid)
        self.imports: dict[str, set[str]] = {p: self._resolve_imports(p, fs.imports) for p, fs in self.files.items()}
        self.importers: dict[str, set[str]] = {}
        for src, targets in self.imports.items():
            for target in targets:
                self.importers.setdefault(target, set()).add(src)
        self.calls: dict[str, set[str]] = {}
        self.callers: dict[str, set[str]] = {}
        for sid, (path, sym) in self.symbols.items():
            for name in sym.calls:
                for target in self.resolve_call(path, name):
                    if target != sid:
                        self.calls.setdefault(sid, set()).add(target)
                        self.callers.setdefault(target, set()).add(sid)

    @classmethod
    def from_tree(cls, root: Path, tree: str, db: DB | None = None) -> "RepoIndex":
        sources = {}
        for path in git.list_tree_files(root, tree):
            if len(sources) >= MAX_INDEX_FILES:
                break
            if supported(path) and not is_excluded(root, path) and "node_modules/" not in path:
                text = git.read_blob(root, tree, path)
                if text is not None and len(text) <= MAX_FILE_BYTES:
                    sources[path] = text
        return cls(sources, db)

    def _resolve_imports(self, path: str, specs: list[str]) -> set[str]:
        out = set()
        base = posixpath.dirname(path)
        for spec in specs:
            if spec.startswith("."):
                if path.endswith(".py"):  # from .x import y
                    dots = len(spec) - len(spec.lstrip("."))
                    parent = base
                    for _ in range(dots - 1):
                        parent = posixpath.dirname(parent)
                    candidate = posixpath.join(parent, spec.lstrip(".").replace(".", "/"))
                    options = [candidate + ".py", candidate + "/__init__.py"]
                else:
                    candidate = posixpath.normpath(posixpath.join(base, spec))
                    stem = candidate.rsplit(".", 1)[0] if candidate.endswith((".js", ".jsx")) else candidate
                    options = [candidate + e for e in RESOLVE_EXTS] + [stem + e for e in RESOLVE_EXTS]
            elif path.endswith(".py"):
                candidate = spec.replace(".", "/")
                options = [candidate + ".py", candidate + "/__init__.py", "src/" + candidate + ".py"]
            else:
                continue  # bare package import: external dependency
            if found := next((o for o in options if o in self.files), None):
                out.add(found)
        return out

    def resolve_call(self, path: str, name: str) -> set[str]:
        candidates = self.by_short_name.get(name, set())
        if len(candidates) <= 3:
            return candidates
        near = {path} | self.imports.get(path, set())
        return {c for c in candidates if c.split("::", 1)[0] in near}

    def expand(self, symbol_id: str, depth: int, direction: str = "callers") -> dict[str, int]:
        return bfs(self.callers if direction == "callers" else self.calls, symbol_id, depth)

    def symbols_in(self, path: str) -> list[str]:
        return [sid for sid in self.symbols if sid.startswith(path + "::")]

    def symbol_source(self, symbol_id: str, max_lines: int) -> str | None:
        if symbol_id not in self.symbols:
            return None
        path, sym = self.symbols[symbol_id]
        lines = self.sources[path].splitlines()[sym.start_line - 1 : sym.end_line]
        clipped = lines[:max_lines] + ([f"... ({len(lines) - max_lines} more lines)"] if len(lines) > max_lines else [])
        return "\n".join(f"{sym.start_line + i:>5} {line}" for i, line in enumerate(clipped))
