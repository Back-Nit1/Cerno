"""Changed-symbol extraction: compare baseline vs final syntax per changed file."""

from cerno.analysis.graph import RepoIndex
from cerno.models import FileChange, SymbolChange


def changed_symbols(changes: list[FileChange], base: RepoIndex, final: RepoIndex) -> list[SymbolChange]:
    out: list[SymbolChange] = []
    for change in changes:
        before_path = change.old_path or change.path
        before = {s.name: s for s in base.files[before_path].symbols} if before_path in base.files else {}
        after = {s.name: s for s in final.files[change.path].symbols} if change.path in final.files else {}
        for name in sorted(before.keys() | after.keys()):
            old, new = before.get(name), after.get(name)
            if old and new and old.body_hash == new.body_hash:
                continue
            kind = "added" if old is None else "deleted" if new is None else "modified"
            sym = new or old
            index, path = (final, change.path) if new else (base, before_path)
            sid = f"{path}::{name}"
            out.append(
                SymbolChange(
                    file_path=change.path, symbol_name=name, symbol_type=sym.kind, change_type=kind,
                    start_line=sym.start_line, end_line=sym.end_line,
                    callers=sorted(index.callers.get(sid, ())), callees=sorted(index.calls.get(sid, ())),
                )
            )
    return out
