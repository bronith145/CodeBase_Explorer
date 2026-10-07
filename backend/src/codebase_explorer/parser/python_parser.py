"""Phase A (ADR-0003): per-file Tree-sitter extraction for Python."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from codebase_explorer.models import (
    BaseRef,
    CallSite,
    Entity,
    ImportRef,
    ParsedFile,
    RouteDef,
    join_qn,
    make_id,
)

_PY = Language(tspython.language())
_DOTTED = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}
_DI_FUNCS = {"Depends", "Security"}


def module_qn_for(path: str) -> tuple[str, bool]:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    is_pkg = bool(parts) and parts[-1] == "__init__"
    if is_pkg:
        parts = parts[:-1]
    return ".".join(parts), is_pkg


def _t(node: Node) -> str:
    return (node.text or b"").decode("utf-8", "replace")


def _string_value(node: Node | None) -> str | None:
    if node is None or node.type != "string":
        return None
    return "".join(_t(c) for c in node.children if c.type == "string_content")


class PythonParser:
    def __init__(self, repo_id: str) -> None:
        self.repo_id = repo_id
        self._parser = Parser(_PY)

    def parse_file(self, file_path: str, source: bytes) -> ParsedFile:
        tree = self._parser.parse(source)
        module_qn, is_pkg = module_qn_for(file_path)
        pf = ParsedFile(
            file_path=file_path,
            module_qn=module_qn,
            is_package=is_pkg,
            file_hash=hashlib.sha256(source).hexdigest(),
            loc=source.count(b"\n") + 1,
        )
        self._pf, self._src = pf, source
        self._walk(tree.root_node, (), None, None, False)
        return pf

    # ------------------------------------------------------------------ walk
    def _walk(
        self, node: Node, scope: tuple[str, ...], cls: str | None,
        caller: str | None, in_class: bool,
    ) -> None:
        t = node.type
        if t == "decorated_definition":
            decos = [c for c in node.children if c.type == "decorator"]
            d = node.child_by_field_name("definition")
            if d is not None and d.type == "function_definition":
                self._function(d, node, decos, scope, cls, in_class)
                return
            if d is not None and d.type == "class_definition":
                self._class(d, node, scope)
                return
        elif t == "function_definition":
            self._function(node, node, [], scope, cls, in_class)
            return
        elif t == "class_definition":
            self._class(node, node, scope)
            return
        elif t == "import_statement":
            self._import(node)
            return
        elif t == "import_from_statement":
            self._import_from(node)
            return
        elif t == "call":
            self._call(node, caller)
        for c in node.children:
            self._walk(c, scope, cls, caller, False)

    # ------------------------------------------------------------ definitions
    def _make_entity(
        self, kind: str, node: Node, outer: Node, scope: tuple[str, ...],
        cls: str | None, is_method: bool,
    ) -> Entity:
        name_node = node.child_by_field_name("name")
        assert name_node is not None
        name = _t(name_node)
        local = ".".join((*scope, name))
        body = node.child_by_field_name("body")
        end = body.start_byte if body is not None else node.end_byte
        signature = self._src[node.start_byte:end].decode("utf-8", "replace").strip()
        signature = signature.rstrip(":").strip()
        return Entity(
            id=make_id(self.repo_id, kind, self._pf.file_path, local),
            kind=kind,
            name=name,
            qualified_name=join_qn(self._pf.module_qn, local),
            local_qn=local,
            file_path=self._pf.file_path,
            start_line=outer.start_point[0] + 1,
            end_line=outer.end_point[0] + 1,
            repo_id=self.repo_id,
            signature=" ".join(signature.split()),
            docstring=self._docstring(body),
            code=self._src[outer.start_byte:outer.end_byte].decode("utf-8", "replace"),
            class_qn=cls,
            is_method=is_method,
        )

    @staticmethod
    def _docstring(body: Node | None) -> str:
        if body is None or not body.named_children:
            return ""
        first = body.named_children[0]
        if first.type == "expression_statement" and first.named_children:
            val = _string_value(first.named_children[0])
            if val is not None:
                return val.strip()
        return ""

    def _function(
        self, node: Node, outer: Node, decos: list[Node], scope: tuple[str, ...],
        cls: str | None, in_class: bool,
    ) -> None:
        ent = self._make_entity("Function", node, outer, scope, cls, in_class and cls is not None)
        self._pf.entities.append(ent)
        for deco in decos:
            self._route(deco, ent)
        inner_scope = (*scope, ent.name)
        for fld in ("parameters", "body"):
            ch = node.child_by_field_name(fld)
            if ch is not None:
                self._walk(ch, inner_scope, cls, ent.qualified_name, False)

    def _class(self, node: Node, outer: Node, scope: tuple[str, ...]) -> None:
        ent = self._make_entity("Class", node, outer, scope, None, False)
        self._pf.entities.append(ent)
        sup = node.child_by_field_name("superclasses")
        if sup is not None:
            for c in sup.named_children:
                if c.type in ("identifier", "attribute") and _DOTTED.match(_t(c)):
                    self._pf.bases.append(BaseRef(ent.qualified_name, _t(c)))
        body = node.child_by_field_name("body")
        if body is not None:
            for c in body.children:
                self._walk(c, (*scope, ent.name), ent.qualified_name, None, True)

    # ----------------------------------------------------------------- routes
    def _route(self, deco: Node, handler: Entity) -> None:
        if not deco.named_children:
            return
        expr = deco.named_children[0]
        if expr.type != "call":
            return
        fn = expr.child_by_field_name("function")
        if fn is None or fn.type != "attribute":
            return
        attr = fn.child_by_field_name("attribute")
        if attr is None or _t(attr) not in _HTTP_METHODS:
            return
        args = expr.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        path = _string_value(first)
        if path is None:
            return
        self._pf.routes.append(
            RouteDef(_t(attr).upper(), path, handler.qualified_name,
                     self._pf.file_path, deco.start_point[0] + 1)
        )

    # ------------------------------------------------------------------ calls
    def _expr(self, node: Node) -> str | None:
        text = _t(node)
        if node.type in ("identifier", "attribute") and _DOTTED.match(text):
            return text
        if node.type == "attribute":
            attr = node.child_by_field_name("attribute")
            obj = node.child_by_field_name("object")
            if attr is None:
                return None
            if obj is not None and obj.type == "call":
                f = obj.child_by_field_name("function")
                if f is not None and _t(f) == "super":
                    return f"super.{_t(attr)}"
            return f"?.{_t(attr)}"
        return None

    def _call(self, node: Node, caller: str | None) -> None:
        fn = node.child_by_field_name("function")
        if caller is None or fn is None:
            return
        line = node.start_point[0] + 1
        expr = self._expr(fn)
        if expr is None:
            return
        self._pf.calls.append(CallSite(caller, expr, line))
        if expr.split(".")[-1] in _DI_FUNCS:
            args = node.child_by_field_name("arguments")
            if args is not None:
                for c in args.named_children:
                    if c.type in ("identifier", "attribute"):
                        dep = self._expr(c)
                        if dep and not dep.startswith("?."):
                            self._pf.calls.append(CallSite(caller, dep, line))
                        break

    # ---------------------------------------------------------------- imports
    def _import(self, node: Node) -> None:
        line = node.start_point[0] + 1
        for c in node.named_children:
            if c.type == "dotted_name":
                self._pf.imports.append(ImportRef(0, _t(c), None, None, line))
            elif c.type == "aliased_import":
                n, a = c.child_by_field_name("name"), c.child_by_field_name("alias")
                if n is not None:
                    self._pf.imports.append(
                        ImportRef(0, _t(n), None, _t(a) if a is not None else None, line)
                    )

    def _import_from(self, node: Node) -> None:
        line = node.start_point[0] + 1
        mod = node.child_by_field_name("module_name")
        level, module = 0, ""
        if mod is not None:
            if mod.type == "relative_import":
                prefix = next((c for c in mod.children if c.type == "import_prefix"), None)
                level = _t(prefix).count(".") if prefix is not None else 1
                dn = next((c for c in mod.children if c.type == "dotted_name"), None)
                module = _t(dn) if dn is not None else ""
            else:
                module = _t(mod)
        for c in node.children_by_field_name("name"):
            if c.type == "dotted_name":
                self._pf.imports.append(ImportRef(level, module, _t(c), None, line))
            elif c.type == "aliased_import":
                n, a = c.child_by_field_name("name"), c.child_by_field_name("alias")
                if n is not None:
                    self._pf.imports.append(
                        ImportRef(level, module, _t(n), _t(a) if a is not None else None, line)
                    )

