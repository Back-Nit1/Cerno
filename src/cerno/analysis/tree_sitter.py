"""Syntax-aware extraction with Tree-sitter: symbols (with source ranges), imports, exports, calls."""

import hashlib
from functools import lru_cache

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from pydantic import BaseModel
from tree_sitter import Language, Node, Parser

LANGUAGES = {
    ".ts": tree_sitter_typescript.language_typescript,
    ".mts": tree_sitter_typescript.language_typescript,
    ".cts": tree_sitter_typescript.language_typescript,
    ".tsx": tree_sitter_typescript.language_tsx,
    ".js": tree_sitter_javascript.language,
    ".jsx": tree_sitter_javascript.language,
    ".mjs": tree_sitter_javascript.language,
    ".cjs": tree_sitter_javascript.language,
    ".py": tree_sitter_python.language,
}

FUNCTION_NODES = {
    "function_declaration", "generator_function_declaration", "method_definition", "function_definition",
}
CLASS_NODES = {"class_declaration", "abstract_class_declaration", "class_definition", "class"}
FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}


class Symbol(BaseModel):
    name: str  # qualified, e.g. "SessionStore.create"
    kind: str  # function | method | class | variable
    start_line: int
    end_line: int
    body_hash: str
    calls: list[str] = []
    exported: bool = False


class FileSyntax(BaseModel):
    language: str
    symbols: list[Symbol]
    imports: list[str]
    exports: list[str]
    module_calls: list[str]


def supported(path: str) -> bool:
    return any(path.endswith(ext) for ext in LANGUAGES)


@lru_cache(maxsize=None)
def _parser(ext: str) -> Parser:
    return Parser(Language(LANGUAGES[ext]()))


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text else ""


def _call_name(node: Node) -> str:
    fn = node.child_by_field_name("function")
    if fn is None:
        return ""
    if fn.type in {"member_expression", "attribute"}:
        prop = fn.child_by_field_name("property") or fn.child_by_field_name("attribute")
        return _text(prop)
    return _text(fn) if fn.type == "identifier" else ""


def _string_value(node: Node | None) -> str:
    return _text(node).strip("'\"`") if node is not None else ""


def parse_source(path: str, source: str) -> FileSyntax | None:
    ext = next((e for e in LANGUAGES if path.endswith(e)), None)
    if ext is None:
        return None
    root = _parser(ext).parse(source.encode()).root_node
    symbols: list[Symbol] = []
    imports: list[str] = []
    exports: list[str] = []
    module_calls: list[str] = []

    def visit(node: Node, scope: list[Symbol], exported: bool) -> None:
        current = scope
        kind = None
        name_node = node.child_by_field_name("name")
        if node.type in FUNCTION_NODES:
            kind = "method" if node.type == "method_definition" or (scope and scope[-1].kind == "class") else "function"
        elif node.type in CLASS_NODES and name_node is not None:
            kind = "class"
        elif node.type == "variable_declarator" and (value := node.child_by_field_name("value")) is not None:
            if value.type in FUNCTION_VALUES:
                kind = "function"
            elif not scope and node.parent is not None and node.parent.parent is not None and (
                node.parent.parent.type in {"program", "export_statement"}
            ):
                kind = "variable"  # top-level constants such as SESSION_TTL_MS matter for behavior
        elif node.type == "expression_statement" and not scope and node.parent is not None and node.parent.type == "module":
            child = node.named_children[0] if node.named_children else None
            if child is not None and child.type == "assignment" and child.child_by_field_name("left").type == "identifier":
                kind, name_node = "variable", child.child_by_field_name("left")

        if kind and (name_node is not None or node.type == "variable_declarator"):
            name_node = name_node if name_node is not None else node.child_by_field_name("name")
            name = _text(name_node)
            if name:
                qual = f"{scope[-1].name}.{name}" if scope else name
                span = node.parent if node.type == "variable_declarator" and node.parent is not None else node
                sym = Symbol(
                    name=qual, kind=kind, start_line=span.start_point[0] + 1, end_line=span.end_point[0] + 1,
                    body_hash=hashlib.sha1(span.text or b"").hexdigest(), exported=exported and not scope,
                )
                symbols.append(sym)
                if sym.exported:
                    exports.append(name)
                current = [*scope, sym]

        if node.type == "import_statement" and ext != ".py":
            imports.append(_string_value(node.child_by_field_name("source")))
        elif node.type == "import_statement":
            imports.extend(_text(n) for n in node.children_by_field_name("name"))
        elif node.type == "import_from_statement":
            imports.append(_text(node.child_by_field_name("module_name")))
        elif node.type in {"call_expression", "call"}:
            name = _call_name(node)
            args = node.child_by_field_name("arguments")
            if name == "require" and args is not None and args.named_children:
                imports.append(_string_value(args.named_children[0]))
            elif name:
                target = current[-1].calls if current else module_calls
                if name not in target:
                    target.append(name)
        elif node.type == "export_statement":
            for spec in node.named_children:
                if spec.type == "export_clause":
                    exports.extend(_text(s.child_by_field_name("name")) for s in spec.named_children)

        is_export = node.type == "export_statement" or (exported and node.type == "lexical_declaration")
        for child in node.children:
            visit(child, current, is_export)

    visit(root, [], False)
    return FileSyntax(
        language=ext.lstrip("."), symbols=symbols, imports=[i for i in imports if i], exports=exports,
        module_calls=module_calls,
    )
