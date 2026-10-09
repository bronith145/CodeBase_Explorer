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
STDLIB_MODULES = set(getattr(sys, "stdlib_module_names", ()))


@dataclass(frozen=True)
class Binding:
    kind: str  # "module" | "symbol" | "external"
    value: str


class Resolver:
    def __init__(self, parsed_files: list[ParsedFile]) -> None:
        self.files: dict[str, ParsedFile] = {parsed_file.file_path: parsed_file for parsed_file in parsed_files}
        self.modules: dict[str, str] = {
            parsed_file.module_qn: parsed_file.file_path
            for parsed_file in parsed_files
            if parsed_file.module_qn
        }

        # Index every module by each of its dotted suffixes for partial-match resolution.
        self.suffix_index: dict[str, list[str]] = defaultdict(list)
        for module_qn in self.modules:
            module_parts = module_qn.split(".")
            for start_index in range(len(module_parts)):
                self.suffix_index[".".join(module_parts[start_index:])].append(module_qn)

        self.by_qualified_name: dict[str, list[Entity]] = defaultdict(list)
        self.methods_by_name: dict[str, list[Entity]] = defaultdict(list)
        # Maps class qualified name → (parsed file, list of raw base-class expressions)
        self._base_expressions: dict[str, tuple[ParsedFile, list[str]]] = {}

        for parsed_file in parsed_files:
            for entity in parsed_file.entities:
                self.by_qualified_name[entity.qualified_name].append(entity)
                if entity.kind == "Function" and entity.is_method:
                    self.methods_by_name[entity.name].append(entity)
            for base_ref in parsed_file.bases:
                self._base_expressions.setdefault(base_ref.class_qn, (parsed_file, []))[1].append(base_ref.expr)

        self._bindings_cache: dict[str, dict[str, Binding]] = {}
        self._base_classes_cache: dict[str, list[str]] = {}

    # ------------------------------------------------------------- module/imports
    def resolve_module(self, dotted_name: str, from_module: str) -> str | None:
        if dotted_name in self.modules:
            return dotted_name
        if dotted_name.split(".")[0] in STDLIB_MODULES:
            return None
        candidates = self.suffix_index.get(dotted_name, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        # Break ties by preferring the candidate with the longest common prefix with the importer.
        source_parts = from_module.split(".")

        def count_common_prefix_parts(module_qn: str) -> int:
            common_count = 0
            for module_part, source_part in zip(module_qn.split("."), source_parts):
                if module_part != source_part:
                    break
                common_count += 1
            return common_count

        return max(candidates, key=count_common_prefix_parts)

    def import_target(self, parsed_file: ParsedFile, import_ref: ImportRef) -> str | None:
        """Return the dotted module name that `import_ref` points to, or None if external/unresolvable."""
        if import_ref.level > 0:
            package_parts = parsed_file.module_qn.split(".") if parsed_file.module_qn else []
            if not parsed_file.is_package:
                package_parts = package_parts[:-1]
            levels_up = import_ref.level - 1
            if levels_up > len(package_parts):
                return None
            if levels_up:
                package_parts = package_parts[:-levels_up]
            return ".".join([*package_parts, *([import_ref.module] if import_ref.module else [])])
        return self.resolve_module(import_ref.module, parsed_file.module_qn)

    def _lookup_member(self, module_qn: str, member_name: str, depth: int) -> Binding | None:
        """Look up `member_name` inside `module_qn`, following re-exports up to MAX_REEXPORT_DEPTH."""
        full_qn = join_qn(module_qn, member_name)
        if full_qn in self.by_qualified_name:
            return Binding("symbol", full_qn)
        if full_qn in self.modules:
            return Binding("module", full_qn)
        if depth < MAX_REEXPORT_DEPTH and module_qn in self.modules:
            reexported_binding = self.bindings(self.modules[module_qn]).get(member_name)
            if reexported_binding is not None and reexported_binding.kind != "external":
                return reexported_binding
        return None

    def bindings(self, file_path: str) -> dict[str, Binding]:
        """Return the local-name → Binding map for a file (cached, cycle-safe)."""
        if file_path in self._bindings_cache:
            return self._bindings_cache[file_path]
        result: dict[str, Binding] = {}
        self._bindings_cache[file_path] = result  # cycle guard
        parsed_file = self.files[file_path]
        for import_ref in parsed_file.imports:
            if import_ref.name is None:  # plain `import a.b [as c]`
                resolved_module = self.resolve_module(import_ref.module, parsed_file.module_qn)
                result[import_ref.alias or import_ref.module] = (
                    Binding("module", resolved_module) if resolved_module
                    else Binding("external", import_ref.module)
                )
                continue
            local_name = import_ref.alias or import_ref.name
            target_module = self.import_target(parsed_file, import_ref)
            resolved_binding: Binding | None = None
            if target_module is not None:
                resolved_binding = self._lookup_member(target_module, import_ref.name, 0)
            else:
                submodule = self.resolve_module(join_qn(import_ref.module, import_ref.name), parsed_file.module_qn)
                if submodule:
                    resolved_binding = Binding("module", submodule)
            result[local_name] = resolved_binding or Binding("external", join_qn(import_ref.module, import_ref.name))
        return result

    # ----------------------------------------------------------------- classes
    def class_entity(self, qualified_name: str) -> Entity | None:
        return next(
            (entity for entity in self.by_qualified_name.get(qualified_name, []) if entity.kind == "Class"),
            None,
        )

    def base_classes(self, class_qn: str) -> list[str]:
        if class_qn in self._base_classes_cache:
            return self._base_classes_cache[class_qn]
        self._base_classes_cache[class_qn] = []  # cycle guard
        resolved_bases: list[str] = []
        if class_qn in self._base_expressions:
            parsed_file, base_expressions = self._base_expressions[class_qn]
            for base_expression in base_expressions:
                lookup_result = self._lookup(base_expression.split("."), None, parsed_file)
                if isinstance(lookup_result, Entity) and lookup_result.kind == "Class":
                    resolved_bases.append(lookup_result.qualified_name)
        self._base_classes_cache[class_qn] = resolved_bases
        return resolved_bases

    def find_method(
        self, class_qn: str, method_name: str, _visited: set[str] | None = None
    ) -> Entity | None:
        visited = _visited if _visited is not None else set()
        if class_qn in visited:
            return None
        visited.add(class_qn)
        for entity in self.by_qualified_name.get(join_qn(class_qn, method_name), []):
            if entity.kind == "Function":
                return entity
        for base_class_qn in self.base_classes(class_qn):
            found = self.find_method(base_class_qn, method_name, visited)
            if found:
                return found
        return None

    # ------------------------------------------------------------------ lookup
    def _apply_attribute_chain(self, entity: Entity, remaining_parts: list[str]) -> Entity | None:
        """Descend into an entity using any remaining dotted parts after the resolved prefix."""
        if not remaining_parts:
            return entity
        if entity.kind == "Class" and len(remaining_parts) == 1:
            return self.find_method(entity.qualified_name, remaining_parts[0])
        return None

    def _lookup(
        self,
        name_parts: list[str],
        caller: Entity | None,
        parsed_file: ParsedFile,
    ) -> Entity | str | None:
        """Resolve a dotted name to an Entity, the string "external", or None (unknown)."""
        head = name_parts[0]

        # Strategy A: enclosing function scopes, then module level.
        scope_parts = caller.local_qn.split(".") if caller else []
        for depth in range(len(scope_parts), -1, -1):
            scope_prefix = ".".join(scope_parts[:depth])
            if depth > 0:
                scope_owners = self.by_qualified_name.get(join_qn(parsed_file.module_qn, scope_prefix), [])
                if not any(owner.kind == "Function" for owner in scope_owners):
                    continue
            scoped_qn = join_qn(parsed_file.module_qn, ".".join([*scope_parts[:depth], head]))
            candidates = self.by_qualified_name.get(scoped_qn, [])
            if candidates:
                return self._apply_attribute_chain(candidates[0], name_parts[1:])

        # Strategy B: import bindings — try longest dotted prefix first.
        file_bindings = self.bindings(parsed_file.file_path)
        for prefix_length in range(len(name_parts), 0, -1):
            prefix_key = ".".join(name_parts[:prefix_length])
            matched_binding = file_bindings.get(prefix_key)
            if matched_binding is None:
                continue
            remaining_parts = name_parts[prefix_length:]
            if matched_binding.kind == "external":
                return "external"
            if matched_binding.kind == "symbol":
                return self._apply_attribute_chain(
                    self.by_qualified_name[matched_binding.value][0], remaining_parts
                )
            # Follow a module chain
            current_module = matched_binding.value
            for attribute_index, attribute_name in enumerate(remaining_parts):
                member_binding = self._lookup_member(current_module, attribute_name, 0)
                if member_binding is None:
                    return None
                if member_binding.kind == "module":
                    current_module = member_binding.value
                    continue
                return self._apply_attribute_chain(
                    self.by_qualified_name[member_binding.value][0],
                    remaining_parts[attribute_index + 1:],
                )
            return None
        return None

    def _heuristic_candidates(self, method_name: str) -> list[tuple[Entity, str]]:
        """Return heuristic matches for an unresolved method name, or [] if too ambiguous."""
        if method_name in COMMON_METHODS or method_name.startswith("__"):
            return []
        candidates = self.methods_by_name.get(method_name, [])
        if 1 <= len(candidates) <= MAX_HEURISTIC_CANDIDATES:
            return [(candidate, "heuristic") for candidate in candidates]
        return []

    def resolve_call(
        self,
        call_expression: str,
        caller: Entity,
        parsed_file: ParsedFile,
    ) -> list[tuple[Entity, str]]:
        parts = call_expression.split(".")

        if parts[0] == "?":
            return self._heuristic_candidates(parts[1])

        if parts[0] in ("self", "cls") and caller.class_qn:
            if len(parts) == 2:
                found_method = self.find_method(caller.class_qn, parts[1])
                if found_method:
                    return [(found_method, "resolved")]
            return self._heuristic_candidates(parts[-1]) if len(parts) > 1 else []

        if parts[0] == "super" and caller.class_qn and len(parts) == 2:
            for base_class_qn in self.base_classes(caller.class_qn):
                found_method = self.find_method(base_class_qn, parts[1])
                if found_method:
                    return [(found_method, "resolved")]
            return []

        lookup_result = self._lookup(parts, caller, parsed_file)
        if isinstance(lookup_result, Entity):
            if lookup_result.kind == "Class":  # instantiation → __init__
                init_method = self.find_method(lookup_result.qualified_name, "__init__")
                return [(init_method, "resolved")] if init_method else []
            return [(lookup_result, "resolved")]
        if lookup_result == "external":
            return []
        return self._heuristic_candidates(parts[-1]) if len(parts) > 1 else []
