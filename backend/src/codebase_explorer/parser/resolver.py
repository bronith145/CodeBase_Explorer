"""Phase B (ADR-0003): repo-wide symbol/call resolution."""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass

from codebase_explorer.models import Entity, ImportRef, ParsedFile, join_qn

COMMON_METHODS = {
    "get", "set", "add", "append", "extend", "items", "keys", "values", "update", "pop",
    "remove", "clear", "copy", "join", "split", "strip", "format", "read", "write", "close",
    "open", "run", "execute", "commit", "flush", "refresh", "delete", "query", "send",
    "start", "stop", "encode", "decode", "lower", "upper", "replace", "find", "index",
    "count", "sort", "insert", "exec", "all", "first", "one", "load", "dump", "dumps",
    "loads", "post", "put", "patch", "head",
}
MAX_HEURISTIC_CANDIDATES = 3
MAX_REEXPORT_DEPTH = 5
STDLIB = set(getattr(sys, "stdlib_module_names", ()))


@dataclass(frozen=True)
class Binding:
    kind: str  # "module" | "symbol" | "external"
    value: str


class Resolver:
    def __init__(self, parsed: list[ParsedFile]) -> None:
        self.files = {pf.file_path: pf for pf in parsed}
        self.modules: dict[str, str] = {
            pf.module_qn: pf.file_path for pf in parsed if pf.module_qn
        }
        self.suffix_index: dict[str, list[str]] = defaultdict(list)
        for m in self.modules:
            parts = m.split(".")
            for i in range(len(parts)):
                self.suffix_index[".".join(parts[i:])].append(m)

        self.by_qn: dict[str, list[Entity]] = defaultdict(list)
        self.methods_by_name: dict[str, list[Entity]] = defaultdict(list)
        self._base_exprs: dict[str, tuple[ParsedFile, list[str]]] = {}
        for pf in parsed:
            for e in pf.entities:
                self.by_qn[e.qualified_name].append(e)
                if e.kind == "Function" and e.is_method:
                    self.methods_by_name[e.name].append(e)
            for b in pf.bases:
                self._base_exprs.setdefault(b.class_qn, (pf, []))[1].append(b.expr)

        self._bindings: dict[str, dict[str, Binding]] = {}
        self._bases_cache: dict[str, list[str]] = {}

    # ------------------------------------------------------------- module/imports
    def resolve_module(self, dotted: str, from_module: str) -> str | None:
        if dotted in self.modules:
            return dotted
        if dotted.split(".")[0] in STDLIB:
            return None
        cands = self.suffix_index.get(dotted, [])
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        src = from_module.split(".")

        def common(m: str) -> int:
            n = 0
            for a, b in zip(m.split("."), src):
                if a != b:
                    break
                n += 1
            return n

        return max(cands, key=common)

    def import_target(self, pf: ParsedFile, imp: ImportRef) -> str | None:
        """Dotted module that `imp` refers to (None if external/unresolvable)."""
        if imp.level > 0:
            pkg = pf.module_qn.split(".") if pf.module_qn else []
            if not pf.is_package:
                pkg = pkg[:-1]
            up = imp.level - 1
            if up > len(pkg):
                return None
            if up:
                pkg = pkg[:-up]
            return ".".join([*pkg, *([imp.module] if imp.module else [])])
        return self.resolve_module(imp.module, pf.module_qn)

    def _member(self, module_qn: str, name: str, depth: int) -> Binding | None:
        qn = join_qn(module_qn, name)
        if qn in self.by_qn:
            return Binding("symbol", qn)
        if qn in self.modules:
            return Binding("module", qn)
        if depth < MAX_REEXPORT_DEPTH and module_qn in self.modules:
            b = self.bindings(self.modules[module_qn]).get(name)
            if b is not None and b.kind != "external":
                return b
        return None

    def bindings(self, file_path: str) -> dict[str, Binding]:
        if file_path in self._bindings:
            return self._bindings[file_path]
        out: dict[str, Binding] = {}
        self._bindings[file_path] = out  # cycle guard
        pf = self.files[file_path]
        for imp in pf.imports:
            if imp.name is None:  # import a.b [as c]
                m = self.resolve_module(imp.module, pf.module_qn)
                out[imp.alias or imp.module] = (
                    Binding("module", m) if m else Binding("external", imp.module)
                )
                continue
            key = imp.alias or imp.name
            target = self.import_target(pf, imp)
            b: Binding | None = None
            if target is not None:
                b = self._member(target, imp.name, 0)
            else:
                sub = self.resolve_module(join_qn(imp.module, imp.name), pf.module_qn)
                if sub:
                    b = Binding("module", sub)
            out[key] = b or Binding("external", join_qn(imp.module, imp.name))
        return out

    # ----------------------------------------------------------------- classes
    def class_entity(self, qn: str) -> Entity | None:
        return next((e for e in self.by_qn.get(qn, []) if e.kind == "Class"), None)

    def base_classes(self, class_qn: str) -> list[str]:
        if class_qn in self._bases_cache:
            return self._bases_cache[class_qn]
        self._bases_cache[class_qn] = []
        result: list[str] = []
        if class_qn in self._base_exprs:
            pf, exprs = self._base_exprs[class_qn]
            for expr in exprs:
                hit = self._lookup(expr.split("."), None, pf)
                if isinstance(hit, Entity) and hit.kind == "Class":
                    result.append(hit.qualified_name)
        self._bases_cache[class_qn] = result
        return result

    def find_method(self, class_qn: str, name: str, _seen: set[str] | None = None) -> Entity | None:
        seen = _seen if _seen is not None else set()
        if class_qn in seen:
            return None
        seen.add(class_qn)
        for e in self.by_qn.get(join_qn(class_qn, name), []):
            if e.kind == "Function":
                return e
        for b in self.base_classes(class_qn):
            hit = self.find_method(b, name, seen)
            if hit:
                return hit
        return None

    # ------------------------------------------------------------------ lookup
    def _apply_rest(self, ent: Entity, rest: list[str]) -> Entity | None:
        if not rest:
            return ent
        if ent.kind == "Class" and len(rest) == 1:
            return self.find_method(ent.qualified_name, rest[0])
        return None

    def _lookup(
        self, parts: list[str], caller: Entity | None, pf: ParsedFile,
    ) -> Entity | str | None:
        """Entity found, "external", or None (unknown)."""
        head = parts[0]
        # a. enclosing function scopes, then module level
        scope = caller.local_qn.split(".") if caller else []
        for i in range(len(scope), -1, -1):
            prefix = ".".join(scope[:i])
            if i > 0:
                owners = self.by_qn.get(join_qn(pf.module_qn, prefix), [])
                if not any(o.kind == "Function" for o in owners):
                    continue
            cands = self.by_qn.get(join_qn(pf.module_qn, ".".join([*scope[:i], head])), [])
            if cands:
                return self._apply_rest(cands[0], parts[1:])
        # b. import bindings (longest dotted prefix first)
        binds = self.bindings(pf.file_path)
        for i in range(len(parts), 0, -1):
            b = binds.get(".".join(parts[:i]))
            if b is None:
                continue
            rest = parts[i:]
            if b.kind == "external":
                return "external"
            if b.kind == "symbol":
                return self._apply_rest(self.by_qn[b.value][0], rest)
            cur = b.value
            for j, name in enumerate(rest):
                mem = self._member(cur, name, 0)
                if mem is None:
                    return None
                if mem.kind == "module":
                    cur = mem.value
                    continue
                return self._apply_rest(self.by_qn[mem.value][0], rest[j + 1:])
            return None
        return None

    def _heuristic(self, name: str) -> list[tuple[Entity, str]]:
        if name in COMMON_METHODS or name.startswith("__"):
            return []
        cands = self.methods_by_name.get(name, [])
        if 1 <= len(cands) <= MAX_HEURISTIC_CANDIDATES:
            return [(c, "heuristic") for c in cands]
        return []

    def resolve_call(
        self, expr: str, caller: Entity, pf: ParsedFile,
    ) -> list[tuple[Entity, str]]:
        parts = expr.split(".")
        if parts[0] == "?":
            return self._heuristic(parts[1])
        if parts[0] in ("self", "cls") and caller.class_qn:
            if len(parts) == 2:
                m = self.find_method(caller.class_qn, parts[1])
                if m:
                    return [(m, "resolved")]
            return self._heuristic(parts[-1]) if len(parts) > 1 else []
        if parts[0] == "super" and caller.class_qn and len(parts) == 2:
            for b in self.base_classes(caller.class_qn):
                m = self.find_method(b, parts[1])
                if m:
                    return [(m, "resolved")]
            return []

        hit = self._lookup(parts, caller, pf)
        if isinstance(hit, Entity):
            if hit.kind == "Class":  # instantiation -> __init__
                init = self.find_method(hit.qualified_name, "__init__")
                return [(init, "resolved")] if init else []
            return [(hit, "resolved")]
        if hit == "external":
            return []
        return self._heuristic(parts[-1]) if len(parts) > 1 else []

